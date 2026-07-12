# frozen_string_literal: true

# =============================================================================
# client.rb — Interface de linha de comando (CLI) da variante Ruby.
#
# Espelha cli.py + client.py do original. É um cliente "fraco": conversa com o
# coordenador (plano de controle) para descobrir o plano, e com os nós (plano de
# dados) para transferir os bytes. Comandos: put, get, list, rm, status.
#
# Fluxo de PUT (escrita):
#   1. RequestUpload no coordenador -> recebe chunk_size + placement por chunk.
#   2. Fatia o arquivo e, para cada chunk, envia STORE ao nó primary (gateway),
#      que faz o fan-out às réplicas com quórum.
#   3. ConfirmUpload no coordenador com as réplicas efetivamente gravadas.
#
# Fluxo de GET (leitura, estilo GFS): o cliente pede o mapa de chunks e busca
# cada pedaço numa réplica viva, remontando o arquivo na ordem.
#
# Uso:
#   ruby client.rb put <arquivo_local> <caminho_dfs>
#   ruby client.rb get <caminho_dfs> <arquivo_local>
#   ruby client.rb list
#   ruby client.rb rm <caminho_dfs>
#   ruby client.rb status
# =============================================================================

require_relative 'config'
require_relative 'lib/protocol'

module DFS
  class Client
    include DFS::Config

    def coord(payload)
      DFS::Protocol.request(COORDINATOR_HOST, COORDINATOR_PORT, payload)
    end

    def put(local_path, dfs_path)
      abort "arquivo local não existe: #{local_path}" unless File.exist?(local_path)

      data = File.binread(local_path)
      plan = coord('op' => 'REQUEST_UPLOAD', 'path' => dfs_path, 'size' => data.bytesize)
      abort "coordenador recusou: #{plan['error']}" unless plan['ok']

      chunk_size = plan['chunk_size']
      confirmed = []
      plan['chunks'].each do |c|
        i = c['index']
        slice = data.byteslice(i * chunk_size, chunk_size) || ''
        replicas = c['replicas']
        primary = replicas.first # gateway do chunk

        resp = DFS::Protocol.request(
          primary['host'], primary['port'],
          'op' => 'STORE', 'chunk_id' => c['chunk_id'],
          'data' => DFS::Protocol.encode_bytes(slice),
          'primary' => true, 'fanout' => replicas
        )
        abort "falha ao gravar chunk #{i}: #{resp['error']}" unless resp['ok']

        stored = resp['stored_on']
        actual = replicas.select { |r| stored.include?(r['node_id']) }
        confirmed << { 'index' => i, 'chunk_id' => c['chunk_id'], 'replicas' => actual }
        puts "  chunk #{i}: gravado em #{stored.join(', ')}"
      end

      coord('op' => 'CONFIRM_UPLOAD', 'path' => dfs_path,
            'chunk_size' => chunk_size, 'chunks' => confirmed)
      puts "OK: #{local_path} -> #{dfs_path} (#{confirmed.size} chunk(s))"
    end

    def get(dfs_path, local_path)
      plan = coord('op' => 'REQUEST_DOWNLOAD', 'path' => dfs_path)
      abort "coordenador: #{plan['error']}" unless plan['ok']

      File.open(local_path, 'wb') do |out|
        plan['chunks'].sort_by { |c| c['index'] }.each do |c|
          bytes = fetch_chunk(c)
          abort "não consegui obter o chunk #{c['index']} de nenhuma réplica viva" if bytes.nil?

          out.write(bytes)
        end
      end
      puts "OK: #{dfs_path} -> #{local_path}"
    end

    def fetch_chunk(chunk)
      chunk['replicas'].each do |r|
        begin
          resp = DFS::Protocol.request(r['host'], r['port'], 'op' => 'FETCH', 'chunk_id' => chunk['chunk_id'])
          return DFS::Protocol.decode_bytes(resp['data']) if resp['ok']
        rescue StandardError
          next # tenta a próxima réplica
        end
      end
      nil
    end

    def list
      resp = coord('op' => 'LIST_FILES')
      files = resp['files'] || []
      if files.empty?
        puts '(nenhum arquivo)'
      else
        puts format('%-30s %6s  %s', 'CAMINHO', 'CHUNKS', 'NÓS')
        files.each { |f| puts format('%-30s %6d  %s', f['path'], f['num_chunks'], f['nodes'].join(',')) }
      end
    end

    def rm(dfs_path)
      resp = coord('op' => 'DELETE_FILE', 'path' => dfs_path)
      abort "erro: #{resp['error']}" unless resp['ok']

      puts "removido: #{dfs_path}"
    end

    def status
      resp = coord('op' => 'STATUS')
      puts "arquivos: #{resp['files']}"
      resp['nodes'].each { |n| puts format('  %-8s %s', n['node_id'], n['state']) }
    end
  end
end

if $PROGRAM_NAME == __FILE__
  cli = DFS::Client.new
  cmd = ARGV[0]
  case cmd
  when 'put'    then cli.put(ARGV[1], ARGV[2])
  when 'get'    then cli.get(ARGV[1], ARGV[2])
  when 'list'   then cli.list
  when 'rm'     then cli.rm(ARGV[1])
  when 'status' then cli.status
  else
    warn 'uso: ruby client.rb [put|get|list|rm|status] ...'
    exit 1
  end
end
