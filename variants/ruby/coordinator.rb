# frozen_string_literal: true

# =============================================================================
# coordinator.rb — Coordenador (plano de controle) da variante Ruby.
#
# Espelha server.py + node_registry.py + metadata_service.py +
# replication_watcher.py do original. Cérebro do sistema:
#   - METADADOS (arquivos -> chunks -> réplicas), persistidos em JSON.
#   - REGISTRO DE NÓS com máquina de vivacidade (ALIVE/SUSPECT/DEAD) preguiçosa.
#   - PLACEMENT determinístico + escolha de INGRESS (round-robin entre nós vivos)
#     e de EGRESS (por localidade: o nó vivo com mais chunks do arquivo).
#   - SUPERVISOR DE RE-REPLICAÇÃO e GARBAGE COLLECTION por block report.
#   - TELEMETRIA: agrega métricas de upload/download reportadas pelos nós
#     ingress/egress (min/máx/média/contagem), no espírito do telemetry_hub.
#
# O coordenador NUNCA toca nos bytes dos arquivos do usuário — só metadados.
# =============================================================================

require 'json'
require 'set'
require 'securerandom'
require_relative 'config'
require_relative 'lib/protocol'
require_relative 'lib/placement'

module DFS
  class Coordinator
    include DFS::Config

    ALIVE = 'ALIVE'
    SUSPECT = 'SUSPECT'
    DEAD = 'DEAD'

    def initialize
      @mutex = Mutex.new
      @nodes = {}            # node_id => { host, port, last_hb, chunks:[] }
      @suspected_orphans = Hash.new { |h, k| h[k] = {} } # node_id => {chunk_id=>count}
      @pending_deletes = Hash.new { |h, k| h[k] = [] }   # node_id => [chunk_id]
      @prev_state = {}       # node_id => estado anterior (p/ detectar transições)
      @upload_counter = 0    # contador monotônico p/ round-robin de ingress

      # Telemetria: agregados por operação + contadores de eventos.
      @metrics = Hash.new { |h, k| h[k] = { 'count' => 0, 'total' => 0.0, 'min' => nil, 'max' => nil, 'bytes' => 0 } }
      @rereplications = 0
      @gc_deletes = 0

      # Membership canônica: a lista OFICIAL dos N nós (base do placement).
      @membership = DFS::Config.node_order
      @node_config = {}
      DFS::Config.build_nodes.each { |n| @node_config[n['node_id']] = n }

      FileUtils.mkdir_p(METADATA_DIR)
      @metadata = load_metadata
    end

    def run
      start_watcher_thread
      log "coordenador no ar em #{COORDINATOR_HOST}:#{COORDINATOR_PORT}"
      log "membership canônica: #{@membership.join(', ')} (RF=#{REPLICATION_FACTOR}, quórum=#{WRITE_QUORUM})"
      DFS::Protocol.serve(COORDINATOR_HOST, COORDINATOR_PORT) { |req| handle(req) }
    end

    private

    # ---- Roteamento das chamadas do plano de controle -----------------------
    def handle(req)
      case req['op']
      when 'REGISTER'         then handle_register(req)
      when 'HEARTBEAT'        then handle_heartbeat(req)
      when 'REQUEST_UPLOAD'   then handle_request_upload(req)
      when 'CONFIRM_UPLOAD'   then handle_confirm_upload(req)
      when 'REQUEST_DOWNLOAD' then handle_request_download(req)
      when 'DELETE_FILE'      then handle_delete_file(req)
      when 'LIST_FILES'       then handle_list_files
      when 'UPDATE_REPLICAS'  then handle_update_replicas(req)
      when 'METRIC'           then handle_metric(req)
      when 'METRICS'          then handle_metrics
      when 'STATUS'           then handle_status
      else { 'ok' => false, 'error' => "op desconhecida: #{req['op']}" }
      end
    end

    # RegisterNode: promove o nó à membership e registra seu endereço.
    def handle_register(req)
      @mutex.synchronize do
        nid = req['node_id']
        @nodes[nid] = { 'host' => req['host'], 'port' => req['port'],
                        'last_hb' => Time.now.to_f, 'chunks' => [] }
        @membership << nid unless @membership.include?(nid)
        @node_config[nid] ||= { 'node_id' => nid, 'host' => req['host'], 'port' => req['port'] }
        @membership = DFS::Placement.sort_nodes(@membership)
      end
      log "nó #{req['node_id']} registrado (membership=#{@membership.size})"
      { 'ok' => true, 'cluster_size' => @membership.size, 'membership' => @membership }
    end

    # Heartbeat: renova a vivacidade, guarda o block report e devolve a lista de
    # órfãos a apagar (deleções pendentes + detecção por dois ciclos).
    def handle_heartbeat(req)
      nid = req['node_id']
      to_delete = []
      @mutex.synchronize do
        node = (@nodes[nid] ||= { 'host' => @node_config.dig(nid, 'host'),
                                  'port' => @node_config.dig(nid, 'port'), 'chunks' => [] })
        node['last_hb'] = Time.now.to_f
        node['chunks'] = Array(req['chunks'])

        pend = @pending_deletes.delete(nid) || []
        to_delete.concat(pend)

        expected = expected_chunks_for(nid)
        seen = node['chunks'].to_set
        suspects = @suspected_orphans[nid]
        current_orphans = seen - expected
        suspects.each_key { |cid| suspects.delete(cid) unless current_orphans.include?(cid) }
        current_orphans.each do |cid|
          suspects[cid] = (suspects[cid] || 0) + 1
          to_delete << cid if suspects[cid] >= 2
        end
        @gc_deletes += to_delete.size
      end
      { 'ok' => true, 'delete' => to_delete.uniq }
    end

    # RequestUpload: calcula chunk_size, placement de cada chunk (membership
    # canônica) e escolhe o INGRESS entre os nós VIVOS (round-robin por arquivo).
    # O cliente NÃO fatia nada — quem fatia e replica é o ingress.
    def handle_request_upload(req)
      size = req['size'].to_i
      upload_id = "up_#{SecureRandom.hex(6)}"
      chunk_size = choose_chunk_size(size, @membership.size)
      num_chunks = size <= 0 ? 1 : (size.to_f / chunk_size).ceil
      num_chunks = 1 if num_chunks.zero?

      chunks = (0...num_chunks).map do |i|
        replica_ids = DFS::Placement.replicas_for_chunk(
          i, @membership, REPLICATION_FACTOR, cluster_size: @membership.size
        )
        live = replica_ids.select { |rid| state_of(rid) != DEAD }
        { 'index' => i, 'chunk_id' => "#{upload_id}_chunk_#{i}",
          'replicas' => replica_ids.map { |rid| addr(rid) }, 'live_count' => live.size }
      end

      insufficient = chunks.find { |c| c['live_count'] < WRITE_QUORUM }
      return { 'ok' => false, 'error' => "réplicas vivas insuficientes p/ quórum no chunk #{insufficient['index']}" } if insufficient

      # Ingress: round-robin ENTRE os nós vivos (o placement usa a canônica).
      live_members = @membership.select { |m| state_of(m) != DEAD }
      return { 'ok' => false, 'error' => 'nenhum nó vivo para ingress' } if live_members.empty?

      idx = nil
      @mutex.synchronize { idx = @upload_counter; @upload_counter += 1 }
      ingress_id = DFS::Placement.sort_nodes(live_members)[idx % live_members.size]

      log "upload #{upload_id} p/ #{req['path']}: #{num_chunks} chunk(s) de #{chunk_size} B, ingress=#{ingress_id}"
      { 'ok' => true, 'upload_id' => upload_id, 'chunk_size' => chunk_size,
        'num_chunks' => num_chunks, 'ingress' => addr(ingress_id), 'chunks' => chunks }
    end

    # ConfirmUpload: registra o arquivo nos metadados com as réplicas efetivas.
    # É chamado pelo INGRESS (que sabe o que gravou), não pelo cliente fraco.
    def handle_confirm_upload(req)
      @mutex.synchronize do
        @metadata['files'][req['path']] = {
          'num_chunks' => req['chunks'].size,
          'chunk_size' => req['chunk_size'],
          'size' => req['size'],
          'created_at' => Time.now.to_f,
          'chunks' => req['chunks'].map do |c|
            { 'index' => c['index'], 'chunk_id' => c['chunk_id'], 'replicas' => c['replicas'] }
          end
        }
        persist_metadata
      end
      log "arquivo #{req['path']} confirmado nos metadados (ingress=#{req['ingress']})"
      { 'ok' => true }
    end

    # RequestDownload: devolve o mapa de chunks e escolhe o EGRESS por LOCALIDADE
    # (o nó vivo que já tem o maior número de chunks do arquivo).
    def handle_request_download(req)
      entry = nil
      @mutex.synchronize { entry = @metadata['files'][req['path']] }
      return { 'ok' => false, 'error' => 'arquivo não encontrado' } unless entry

      chunks = entry['chunks'].map do |c|
        { 'index' => c['index'], 'chunk_id' => c['chunk_id'], 'replicas' => c['replicas'] }
      end

      counts = Hash.new(0)
      entry['chunks'].each do |c|
        c['replicas'].each { |r| counts[r['node_id']] += 1 if state_of(r['node_id']) != DEAD }
      end
      return { 'ok' => false, 'error' => 'nenhuma réplica viva para servir o download' } if counts.empty?

      egress_id = counts.max_by { |_nid, cnt| cnt }.first
      log "download #{req['path']}: egress=#{egress_id} (#{counts[egress_id]} de #{entry['num_chunks']} chunks locais)"
      { 'ok' => true, 'num_chunks' => entry['num_chunks'], 'egress' => addr(egress_id), 'chunks' => chunks }
    end

    def handle_delete_file(req)
      entry = nil
      @mutex.synchronize { entry = @metadata['files'][req['path']] }
      return { 'ok' => false, 'error' => 'arquivo não encontrado' } unless entry

      entry['chunks'].each do |c|
        c['replicas'].each do |r|
          rid = r['node_id']
          if state_of(rid) == DEAD
            @mutex.synchronize { @pending_deletes[rid] << c['chunk_id'] }
          else
            begin
              DFS::Protocol.request(r['host'], r['port'], 'op' => 'DELETE', 'chunk_id' => c['chunk_id'])
            rescue StandardError
              @mutex.synchronize { @pending_deletes[rid] << c['chunk_id'] }
            end
          end
        end
      end
      @mutex.synchronize do
        @metadata['files'].delete(req['path'])
        persist_metadata
      end
      log "arquivo #{req['path']} removido"
      { 'ok' => true }
    end

    def handle_list_files
      files = @mutex.synchronize do
        @metadata['files'].map do |path, e|
          nodes = e['chunks'].flat_map { |c| c['replicas'].map { |r| r['node_id'] } }.uniq.sort
          { 'path' => path, 'num_chunks' => e['num_chunks'], 'size' => e['size'], 'nodes' => nodes }
        end
      end
      { 'ok' => true, 'files' => files }
    end

    def handle_update_replicas(req)
      @mutex.synchronize do
        entry = @metadata['files'][req['path']]
        if entry
          chunk = entry['chunks'].find { |c| c['chunk_id'] == req['chunk_id'] }
          chunk['replicas'] = req['replicas'] if chunk
          persist_metadata
        end
      end
      { 'ok' => true }
    end

    # METRIC: um nó ingress/egress reporta a duração e o volume de uma operação.
    def handle_metric(req)
      op = req['metric'] # 'upload' | 'download'
      @mutex.synchronize do
        m = @metrics[op]
        dur = req['duration'].to_f
        m['count'] += 1
        m['total'] += dur
        m['bytes'] += req['bytes'].to_i
        m['min'] = dur if m['min'].nil? || dur < m['min']
        m['max'] = dur if m['max'].nil? || dur > m['max']
      end
      { 'ok' => true }
    end

    def handle_metrics
      @mutex.synchronize do
        ops = {}
        @metrics.each do |op, m|
          avg = m['count'].zero? ? 0.0 : m['total'] / m['count']
          ops[op] = { 'count' => m['count'], 'avg_ms' => (avg * 1000).round(2),
                      'min_ms' => ((m['min'] || 0) * 1000).round(2),
                      'max_ms' => ((m['max'] || 0) * 1000).round(2),
                      'bytes' => m['bytes'] }
        end
        { 'ok' => true, 'ops' => ops, 'rereplications' => @rereplications,
          'gc_deletes' => @gc_deletes, 'files' => @metadata['files'].size }
      end
    end

    def handle_status
      st = @membership.map { |nid| { 'node_id' => nid, 'state' => state_of(nid) } }
      { 'ok' => true, 'nodes' => st, 'files' => @metadata['files'].size,
        'rereplications' => @rereplications, 'gc_deletes' => @gc_deletes }
    end

    # ---- Vivacidade (máquina de 3 estados, cálculo preguiçoso) --------------
    def state_of(node_id)
      node = @nodes[node_id]
      return DEAD if node.nil? || node['last_hb'].nil?

      silence = Time.now.to_f - node['last_hb']
      if silence < HEARTBEAT_SUSPECT then ALIVE
      elsif silence < HEARTBEAT_DEAD then SUSPECT
      else DEAD
      end
    end

    def addr(node_id)
      n = @nodes[node_id] || @node_config[node_id] || {}
      { 'node_id' => node_id, 'host' => n['host'], 'port' => n['port'] }
    end

    def expected_chunks_for(node_id)
      set = Set.new
      @metadata['files'].each_value do |e|
        e['chunks'].each do |c|
          set << c['chunk_id'] if c['replicas'].any? { |r| r['node_id'] == node_id }
        end
      end
      set
    end

    # ---- Supervisor de re-replicação ----------------------------------------
    def start_watcher_thread
      Thread.new do
        loop do
          sleep WATCHER_INTERVAL
          begin
            detect_and_heal
          rescue StandardError => e
            log "watcher erro: #{e.message}"
          end
        end
      end
    end

    def detect_and_heal
      transitions = []
      @mutex.synchronize do
        @membership.each do |nid|
          cur = state_of(nid)
          transitions << nid if cur == DEAD && @prev_state[nid] != DEAD
          @prev_state[nid] = cur
        end
      end
      return if transitions.empty?

      transitions.each { |nid| log "detectada MORTE de #{nid}: iniciando re-replicação" }

      work = []
      @mutex.synchronize do
        @metadata['files'].each do |path, e|
          e['chunks'].each do |c|
            replica_ids = c['replicas'].map { |r| r['node_id'] }
            dead = replica_ids.select { |rid| state_of(rid) == DEAD }
            next if dead.empty?

            live = replica_ids.select { |rid| state_of(rid) != DEAD }
            next if live.empty?

            need = REPLICATION_FACTOR - live.size
            next if need <= 0

            candidates = @membership.select { |m| state_of(m) != DEAD && !replica_ids.include?(m) }
            candidates.first(need).each do |target|
              work << { path: path, chunk: c, source: live.first, target: target }
            end
          end
        end
      end

      work.each { |w| replicate_chunk(w) }
    end

    def replicate_chunk(w)
      src = addr(w[:source])
      tgt = addr(w[:target])
      resp = DFS::Protocol.request(src['host'], src['port'],
                                   'op' => 'REPLICATE', 'chunk_id' => w[:chunk]['chunk_id'],
                                   'target' => tgt)
      return unless resp['ok']

      @mutex.synchronize do
        new_replicas = w[:chunk]['replicas'].reject { |r| state_of(r['node_id']) == DEAD }
        new_replicas << tgt
        w[:chunk]['replicas'] = new_replicas.uniq { |r| r['node_id'] }
        @rereplications += 1
        persist_metadata
      end
      log "chunk #{w[:chunk]['chunk_id']} re-replicado #{w[:source]} -> #{w[:target]}"
    rescue StandardError => e
      log "falha ao re-replicar #{w[:chunk]['chunk_id']}: #{e.message}"
    end

    # ---- Tamanho de chunk adaptável (porta de chunking.py) ------------------
    def choose_chunk_size(file_size, cluster_size)
      return MIN_CHUNK_SIZE if file_size <= 0

      candidate = file_size / (cluster_size * CHUNK_TARGET_MULTIPLIER)
      candidate = [candidate, file_size / cluster_size].min if file_size >= cluster_size * MIN_CHUNK_SIZE
      [MIN_CHUNK_SIZE, [candidate, MAX_CHUNK_SIZE].min].max
    end

    # ---- Persistência de metadados ------------------------------------------
    def load_metadata
      if File.exist?(METADATA_FILE)
        JSON.parse(File.read(METADATA_FILE))
      else
        { 'files' => {} }
      end
    rescue StandardError
      { 'files' => {} }
    end

    def persist_metadata
      File.write(METADATA_FILE, JSON.pretty_generate(@metadata))
    end

    def log(msg)
      puts "[coordenador] #{msg}"
    end
  end
end

if $PROGRAM_NAME == __FILE__
  $stdout.sync = true
  DFS::Coordinator.new.run
end
