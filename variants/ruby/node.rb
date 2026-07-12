# frozen_string_literal: true

# =============================================================================
# node.rb — Nó de armazenamento (plano de dados) da variante Ruby.
#
# Espelha storage_node.py + data_service.py + local_storage.py do original.
# Papéis do nó:
#   - Armazenador: grava/lê/apaga chunks no seu diretório em disco.
#   - Réplica: recebe cópias de chunks de outros nós (fan-out).
#   - Gateway/primary: ao receber um STORE com fan-out, grava localmente e
#     dispara StoreChunk aos demais nós-réplica, exigindo o quórum de escrita.
#   - Heartbeat: envia batimentos periódicos ao coordenador com block report,
#     e recebe de volta a lista de órfãos a apagar (garbage collection).
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

    # ---- Roteamento das operações do plano de dados -------------------------
    def handle(req)
      case req['op']
      when 'PING'        then { 'ok' => true, 'node_id' => @node_id }
      when 'STORE'       then handle_store(req)
      when 'FETCH'       then handle_fetch(req)
      when 'DELETE'      then handle_delete(req)
      when 'LIST'        then { 'ok' => true, 'chunks' => local_chunks }
      when 'REPLICATE'   then handle_replicate(req)
      else { 'ok' => false, 'error' => "op desconhecida: #{req['op']}" }
      end
    end

    # STORE grava um chunk. Se vier `fanout` (lista de réplicas) e `primary` for
    # true, este nó atua como gateway: grava local e replica aos demais, exigindo
    # o quórum de escrita (WRITE_QUORUM de REPLICATION_FACTOR).
    def handle_store(req)
      chunk_id = req['chunk_id']
      data = DFS::Protocol.decode_bytes(req['data'])
      write_chunk(chunk_id, data)

      unless req['primary']
        return { 'ok' => true, 'stored_on' => [@node_id] }
      end

      # Papel de primary: fan-out às demais réplicas.
      stored_on = [@node_id]
      others = (req['fanout'] || []).reject { |r| r['node_id'] == @node_id }
      others.each do |r|
        begin
          resp = DFS::Protocol.request(r['host'], r['port'],
                                       'op' => 'STORE', 'chunk_id' => chunk_id,
                                       'data' => req['data'], 'primary' => false)
          stored_on << r['node_id'] if resp['ok']
        rescue StandardError => e
          log "fan-out falhou p/ #{r['node_id']}: #{e.message}"
        end
      end

      if stored_on.size >= WRITE_QUORUM
        { 'ok' => true, 'stored_on' => stored_on }
      else
        { 'ok' => false, 'error' => "quórum não atingido (#{stored_on.size}/#{WRITE_QUORUM})",
          'stored_on' => stored_on }
      end
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

    # REPLICATE (cura de fundo, substitui o comando Kafka de re-replicação):
    # o coordenador manda este nó-fonte copiar um chunk para um nó de destino.
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

    # ---- Heartbeat + garbage collection -------------------------------------
    def start_heartbeat_thread
      # Registro inicial no coordenador.
      register_with_retry
      Thread.new do
        loop do
          sleep HEARTBEAT_INTERVAL
          begin
            resp = DFS::Protocol.request(
              COORDINATOR_HOST, COORDINATOR_PORT,
              'op' => 'HEARTBEAT', 'node_id' => @node_id, 'chunks' => local_chunks
            )
            # Resposta pode trazer órfãos a apagar (GC via block report).
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
          log "registrado no coordenador"
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
