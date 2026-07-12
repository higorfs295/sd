# frozen_string_literal: true

# =============================================================================
# test_unit.rb — Testes de unidade das funções puras (variante Ruby).
#
# Espelha test_chunking.py + a validação de placement do original. Não precisa
# do cluster no ar: exercita a regra de posicionamento determinístico e o
# dimensionamento adaptável de chunk.
#
# Uso: ruby test_unit.rb
# =============================================================================

require_relative 'config'
require_relative 'lib/placement'

$failures = 0
def check(desc)
  ok = yield
  puts "#{ok ? 'ok  ' : 'FALHA'} - #{desc}"
  $failures += 1 unless ok
end

nodes = %w[node1 node2 node3 node4 node5]

# Placement determinístico: chunk i -> nós i, i+1, i+2 (mod N).
check('chunk 0 -> node1,node2,node3') do
  DFS::Placement.replicas_for_chunk(0, nodes, 3, cluster_size: 5) == %w[node1 node2 node3]
end
check('chunk 3 dá a volta -> node4,node5,node1') do
  DFS::Placement.replicas_for_chunk(3, nodes, 3, cluster_size: 5) == %w[node4 node5 node1]
end
check('determinístico: mesma entrada, mesma saída') do
  DFS::Placement.replicas_for_chunk(7, nodes, 3) == DFS::Placement.replicas_for_chunk(7, nodes, 3)
end
check('réplicas sempre distintas') do
  r = DFS::Placement.replicas_for_chunk(2, nodes, 3, cluster_size: 5)
  r.uniq.size == r.size
end
check('ordenação numérica: node2 antes de node10') do
  DFS::Placement.sort_nodes(%w[node10 node2 node1]) == %w[node1 node2 node10]
end
check('cluster_size divergente estoura (blindagem)') do
  begin
    DFS::Placement.replicas_for_chunk(0, nodes, 3, cluster_size: 4)
    false
  rescue ArgumentError
    true
  end
end

# Dimensionamento de chunk (mesma lógica do coordenador).
def chunk_size(file_size, cluster_size)
  return DFS::Config::MIN_CHUNK_SIZE if file_size <= 0

  c = file_size / (cluster_size * DFS::Config::CHUNK_TARGET_MULTIPLIER)
  c = [c, file_size / cluster_size].min if file_size >= cluster_size * DFS::Config::MIN_CHUNK_SIZE
  [DFS::Config::MIN_CHUNK_SIZE, [c, DFS::Config::MAX_CHUNK_SIZE].min].max
end

check('arquivo pequeno -> piso MIN_CHUNK_SIZE') do
  chunk_size(1000, 5) == DFS::Config::MIN_CHUNK_SIZE
end
check('chunk nunca abaixo do piso') do
  chunk_size(50 * 1024 * 1024, 5) >= DFS::Config::MIN_CHUNK_SIZE
end
check('chunk nunca acima do teto') do
  chunk_size(10_000 * 1024 * 1024, 5) <= DFS::Config::MAX_CHUNK_SIZE
end

puts($failures.zero? ? "\nTODOS OS TESTES PASSARAM" : "\n#{$failures} TESTE(S) FALHARAM")
exit($failures.zero? ? 0 : 1)
