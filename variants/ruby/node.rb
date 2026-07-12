# frozen_string_literal: true

# =============================================================================
# node.rb — Nó de armazenamento (plano de dados) da variante Ruby.
#
# Espelha storage_node.py + data_service.py + local_storage.py do original.
# Papéis do nó:
#   - Armazenador: grava/lê/apaga chunks no seu diretório em disco.
#   - Réplica: recebe cópias de chunks de outros nós (fan-out).
#   - INGRESS (UPLOAD_FILE): recebe o arquivo inteiro do cliente, fatia em chunks
#     do tamanho planejado, grava os que lhe cabem, faz o fan-out às réplicas com
#     quórum, e CONFIRMA o upload ao coordenador (o cliente é fraco).
#   - EGRESS (DOWNLOAD_FILE): reúne os chunks (locais + buscados em peers por
#     localidade) e devolve o arquivo montado ao cliente.
#   - Heartbeat: batimentos periódicos com block report; recebe a lista de
#     órfãos a apagar (garbage collection).
#
# Uso: ruby node.rb <node_id> <port> <storage_dir>
# =============================================================================

require_relative 'config'
require_relative 'lib/protocol'

module DFS
  class StorageNode
    include DFS::Config

    def initialize(node_id, host, port, storage_dir)
      @node_id = node_id
      @host = host
      @port = port
      @chunks_dir = File.join(storage_dir, 'chunks')
      FileUtils.mkdir_p(@chunks_dir)
    end

    def run
      start_heartbeat_thread
      log "nó no ar em #{@host}:#{@port} (dir=#{@chunks_dir})"
      DFS::Protocol.serve(@host, @port) { |req| handle(req) }
    end

    private

    def handle(req)
      case req['op']
      when 'PING'          then { 'ok' => true, 'node_id' => @node_id }
      when 'UPLOAD_FILE'   then handle_upload_file(req)   # papel de ingress
      when 'DOWNLOAD_FILE' then handle_download_file(req) # papel de egress
      when 'STORE'         then handle_store(req)         # fan-out entre nós
      when 'FETCH'         then handle_fetch(req)
      when 'DELETE'        then handle_delete(req)
      when 'LIST'          then { 'ok' => true, 'chunks' => local_chunks }
      when 'REPLICATE'     then handle_replicate(req)
      else { 'ok' => false, 'error' => "op desconhecida: #{req['op']}" }
      end
    end

    # ---- Papel de INGRESS (UPLOAD_FILE) -------------------------------------
    # Recebe o arquivo inteiro + o plano; fatia, grava/replica com quórum e
    # confirma ao coordenador. Espelha DataServicer.UploadFile do original.
    def handle_upload_file(req)
      t0 = Time.now
      data = DFS::Protocol.decode_bytes(req['data'])
      chunk_size = req['chunk_size']
      plan = req['chunks'] # [{index, chunk_id, replicas:[addr]}]
      confirmed = []

      plan.sort_by { |c| c['index'] }.each do |c|
        i = c['index']
        slice = data.byteslice(i * chunk_size, chunk_size) || ''
        replicas = c['replicas']
        # grava local se este nó é uma das réplicas
        write_chunk(c['chunk_id'], slice) if replicas.any? { |r| r['node_id'] == @node_id }
        # fan-out às demais réplicas, exigindo quórum
        stored = fan_out(c['chunk_id'], slice, replicas)
        return { 'ok' => false, 'error' => "quórum não atingido no chunk #{i}" } if stored.size < [WRITE_QUORUM, replicas.size].min

        actual = replicas.select { |r| stored.include?(r['node_id']) }
        confirmed << { 'index' => i, 'chunk_id' => c['chunk_id'], 'replicas' => actual }
      end

      # O INGRESS confirma ao coordenador (cliente fraco não confirma).
      DFS::Protocol.request(COORDINATOR_HOST, COORDINATOR_PORT,
                            'op' => 'CONFIRM_UPLOAD', 'path' => req['path'],
                            'chunk_size' => chunk_size, 'size' => data.bytesize,
                            'ingress' => @node_id, 'chunks' => confirmed)

      dur = Time.now - t0
      emit_metric('upload', dur, data.bytesize)
      log "ingress: #{req['path']} (#{confirmed.size} chunk(s), #{data.bytesize} B) confirmado"
      { 'ok' => true, 'chunks_written' => confirmed.size, 'bytes' => data.bytesize, 'chunks' => confirmed }
    end

    # Fan-out de um chunk às suas réplicas (menos este nó, que já gravou local).
    # Devolve a lista de node_ids que confirmaram a gravação.
    def fan_out(chunk_id, data, replicas)
      stored = []
      stored << @node_id if replicas.any? { |r| r['node_id'] == @node_id } && File.exist?(chunk_path(chunk_id))
      b64 = DFS::Protocol.encode_bytes(data)
      replicas.reject { |r| r['node_id'] == @node_id }.each do |r|
        begin
          resp = DFS::Protocol.request(r['host'], r['port'],
                                       'op' => 'STORE', 'chunk_id' => chunk_id,
                                       'data' => b64, 'primary' => false)
          stored << r['node_id'] if resp['ok']
        rescue StandardError => e
          log "fan-out falhou p/ #{r['node_id']}: #{e.message}"
        end
      end
      stored
    end

    # ---- Papel de EGRESS (DOWNLOAD_FILE) ------------------------------------
    # Reúne os chunks (locais + buscados em peers) e devolve o arquivo montado.
    # Espelha DataServicer.DownloadFile do original.
    def handle_download_file(req)
      t0 = Time.now
      buffer = +''
      req['chunks'].sort_by { |c| c['index'] }.each do |c|
        bytes = if File.exist?(chunk_path(c['chunk_id']))
                  File.binread(chunk_path(c['chunk_id']))
                else
                  fetch_from_peer(c)
                end
        return { 'ok' => false, 'error' => "chunk #{c['index']} indisponível em todas as réplicas" } if bytes.nil?

        buffer << bytes
      end
      dur = Time.now - t0
      emit_metric('download', dur, buffer.bytesize)
      log "egress: servindo #{req['chunks'].size} chunk(s), #{buffer.bytesize} B"
      { 'ok' => true, 'data' => DFS::Protocol.encode_bytes(buffer), 'bytes' => buffer.bytesize }
    end

    def fetch_from_peer(chunk)
      chunk['replicas'].reject { |r| r['node_id'] == @node_id }.each do |r|
        begin
          resp = DFS::Protocol.request(r['host'], r['port'], 'op' => 'FETCH', 'chunk_id' => chunk['chunk_id'])
          return DFS::Protocol.decode_bytes(resp['data']) if resp['ok']
        rescue StandardError
          next
        end
      end
      nil
    end

    # ---- Operações nó-a-nó --------------------------------------------------
    def handle_store(req)
      write_chunk(req['chunk_id'], DFS::Protocol.decode_bytes(req['data']))
      { 'ok' => true, 'stored_on' => [@node_id] }
    end

    def handle_fetch(req)
      path = chunk_path(req['chunk_id'])
      return { 'ok' => false, 'error' => 'chunk ausente' } unless File.exist?(path)

      { 'ok' => true, 'data' => DFS::Protocol.encode_bytes(File.binread(path)) }
    end

    def handle_delete(req)
      path = chunk_path(req['chunk_id'])
      File.delete(path) if File.exist?(path)
      { 'ok' => true }
    end

    # REPLICATE (cura de fundo, substitui o comando Kafka de re-replicação).
    def handle_replicate(req)
      chunk_id = req['chunk_id']
      path = chunk_path(chunk_id)
      return { 'ok' => false, 'error' => 'fonte não tem o chunk' } unless File.exist?(path)

      target = req['target']
      data = DFS::Protocol.encode_bytes(File.binread(path))
      resp = DFS::Protocol.request(target['host'], target['port'],
                                   'op' => 'STORE', 'chunk_id' => chunk_id,
                                   'data' => data, 'primary' => false)
      if resp['ok']
        log "re-replicou #{chunk_id} -> #{target['node_id']}"
        { 'ok' => true, 'target' => target['node_id'] }
      else
        { 'ok' => false, 'error' => "destino recusou: #{resp['error']}" }
      end
    end

    # ---- Persistência local -------------------------------------------------
    def chunk_path(chunk_id)
      File.join(@chunks_dir, chunk_id)
    end

    def write_chunk(chunk_id, data)
      File.binwrite(chunk_path(chunk_id), data)
    end

    def local_chunks
      Dir.children(@chunks_dir).select { |f| File.file?(chunk_path(f)) }
    rescue StandardError
      []
    end

    # ---- Telemetria ---------------------------------------------------------
    def emit_metric(metric, duration, bytes)
      DFS::Protocol.request(COORDINATOR_HOST, COORDINATOR_PORT,
                            'op' => 'METRIC', 'metric' => metric,
                            'duration' => duration, 'bytes' => bytes, 'node_id' => @node_id)
    rescue StandardError
      # telemetria é best-effort
    end

    # ---- Heartbeat + garbage collection -------------------------------------
    def start_heartbeat_thread
      register_with_retry
      Thread.new do
        loop do
          sleep HEARTBEAT_INTERVAL
          begin
            resp = DFS::Protocol.request(
              COORDINATOR_HOST, COORDINATOR_PORT,
              'op' => 'HEARTBEAT', 'node_id' => @node_id, 'chunks' => local_chunks
            )
            Array(resp['delete']).each do |cid|
              p = chunk_path(cid)
              if File.exist?(p)
                File.delete(p)
                log "GC apagou órfão #{cid}"
              end
            end
          rescue StandardError => e
            log "heartbeat falhou: #{e.message}"
          end
        end
      end
    end

    def register_with_retry
      10.times do
        begin
          DFS::Protocol.request(
            COORDINATOR_HOST, COORDINATOR_PORT,
            'op' => 'REGISTER', 'node_id' => @node_id, 'host' => @host, 'port' => @port
          )
          log 'registrado no coordenador'
          return
        rescue StandardError
          sleep 1
        end
      end
      log 'não consegui registrar (coordenador fora do ar?)'
    end

    def log(msg)
      puts "[#{@node_id}] #{msg}"
    end
  end
end

if $PROGRAM_NAME == __FILE__
  $stdout.sync = true
  node_id = ARGV[0] or abort 'uso: ruby node.rb <node_id> <port> <storage_dir>'
  port = (ARGV[1] || abort('faltou a porta')).to_i
  storage_dir = ARGV[2] || File.join(DFS::Config::NODES_DIR, node_id)
  DFS::StorageNode.new(node_id, DFS::Config::NODE_HOST, port, storage_dir).run
end
