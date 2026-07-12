# frozen_string_literal: true

# =============================================================================
# client.rb — Interface de linha de comando (CLI) da variante Ruby.
#
# Espelha cli.py + client.py do original. É um CLIENTE FRACO: não fatia arquivos,
# não decide posicionamento e não conhece a topologia. Ele apenas:
#   - fala com o coordenador (plano de controle) para descobrir o ingress/egress;
#   - entrega/recebe o arquivo INTEIRO a/do nó gateway (plano de dados).
#
# PUT: RequestUpload -> envia o arquivo inteiro ao INGRESS (que fatia, replica
#      com quórum e confirma ao coordenador).
# GET: RequestDownload -> pede o arquivo ao EGRESS (que remonta a partir das
#      réplicas, por localidade) e grava no disco local.
#
# Uso:
#   ruby client.rb put <arquivo_local> <caminho_dfs>
#   ruby client.rb get <caminho_dfs> <arquivo_local>
#   ruby client.rb list | rm <caminho_dfs> | status | metrics
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

      ingress = plan['ingress']
      # Entrega o arquivo INTEIRO ao ingress; ele fatia, replica e confirma.
      resp = DFS::Protocol.request(
        ingress['host'], ingress['port'],
        'op' => 'UPLOAD_FILE', 'path' => dfs_path,
        'upload_id' => plan['upload_id'], 'chunk_size' => plan['chunk_size'],
        'chunks' => plan['chunks'], 'data' => DFS::Protocol.encode_bytes(data)
      )
      abort "falha no upload (ingress #{ingress['node_id']}): #{resp['error']}" unless resp['ok']

      puts "OK: #{local_path} -> #{dfs_path} via ingress #{ingress['node_id']} " \
           "(#{resp['chunks_written']} chunk(s), #{resp['bytes']} B)"
    end

    def get(dfs_path, local_path)
      plan = coord('op' => 'REQUEST_DOWNLOAD', 'path' => dfs_path)
      abort "coordenador: #{plan['error']}" unless plan['ok']

      egress = plan['egress']
      resp = DFS::Protocol.request(
        egress['host'], egress['port'],
        'op' => 'DOWNLOAD_FILE', 'path' => dfs_path, 'chunks' => plan['chunks']
      )
      abort "falha no download (egress #{egress['node_id']}): #{resp['error']}" unless resp['ok']

      File.binwrite(local_path, DFS::Protocol.decode_bytes(resp['data']))
      puts "OK: #{dfs_path} -> #{local_path} via egress #{egress['node_id']} (#{resp['bytes']} B)"
    end

    def list
      resp = coord('op' => 'LIST_FILES')
      files = resp['files'] || []
      if files.empty?
        puts '(nenhum arquivo)'
      else
        puts format('%-28s %6s %10s  %s', 'CAMINHO', 'CHUNKS', 'BYTES', 'NÓS')
        files.each { |f| puts format('%-28s %6d %10s  %s', f['path'], f['num_chunks'], f['size'] || '-', f['nodes'].join(',')) }
      end
    end

    def rm(dfs_path)
      resp = coord('op' => 'DELETE_FILE', 'path' => dfs_path)
      abort "erro: #{resp['error']}" unless resp['ok']

      puts "removido: #{dfs_path}"
    end

    def status
      resp = coord('op' => 'STATUS')
      puts "arquivos: #{resp['files']} | re-replicações: #{resp['rereplications']} | GC: #{resp['gc_deletes']}"
      resp['nodes'].each { |n| puts format('  %-8s %s', n['node_id'], n['state']) }
    end

    def metrics
      resp = coord('op' => 'METRICS')
      puts "arquivos: #{resp['files']} | re-replicações: #{resp['rereplications']} | GC apagou: #{resp['gc_deletes']}"
      ops = resp['ops'] || {}
      if ops.empty?
        puts '(sem métricas de operação ainda)'
      else
        puts format('%-10s %6s %10s %10s %10s %12s', 'OP', 'N', 'AVG(ms)', 'MIN(ms)', 'MAX(ms)', 'BYTES')
        ops.each do |op, m|
          puts format('%-10s %6d %10.2f %10.2f %10.2f %12d', op, m['count'], m['avg_ms'], m['min_ms'], m['max_ms'], m['bytes'])
        end
      end
    end
  end
end

if $PROGRAM_NAME == __FILE__
  cli = DFS::Client.new
  case ARGV[0]
  when 'put'     then cli.put(ARGV[1], ARGV[2])
  when 'get'     then cli.get(ARGV[1], ARGV[2])
  when 'list'    then cli.list
  when 'rm'      then cli.rm(ARGV[1])
  when 'status'  then cli.status
  when 'metrics' then cli.metrics
  else
    warn 'uso: ruby client.rb [put|get|list|rm|status|metrics] ...'
    exit 1
  end
end
