# frozen_string_literal: true

# =============================================================================
# run_cluster.rb — Orquestrador do cluster (variante Ruby).
#
# Espelha run_cluster.py: sobe o coordenador e os N nós como PROCESSOS
# independentes (cada um com seu servidor TCP e seu diretório em disco),
# preservando a independência de processo de um sistema distribuído real.
# Encerra tudo com Ctrl+C.
# =============================================================================

require_relative 'config'

$stdout.sync = true
pids = []
here = __dir__
ruby = RbConfig.ruby

at_exit do
  puts "\n[run_cluster] encerrando cluster..."
  pids.each do |pid|
    begin
      Process.kill('KILL', pid)
    rescue StandardError
      nil
    end
  end
end

Signal.trap('INT') { exit }

puts '[run_cluster] subindo o coordenador...'
pids << spawn(ruby, File.join(here, 'coordinator.rb'))
sleep 1.5 # dá tempo do coordenador abrir a porta antes dos nós registrarem

DFS::Config.build_nodes.each do |n|
  puts "[run_cluster] subindo #{n['node_id']} na porta #{n['port']}..."
  pids << spawn(ruby, File.join(here, 'node.rb'), n['node_id'], n['port'].to_s, n['storage_dir'])
  sleep 0.3
end

puts '[run_cluster] ecossistema DFS operacional. Ctrl+C para encerrar.'
puts '[run_cluster] em outro terminal:  ruby client.rb put <arquivo> /destino'
Process.waitall
