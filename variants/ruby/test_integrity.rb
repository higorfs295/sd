# frozen_string_literal: true

# =============================================================================
# test_integrity.rb — Teste de integridade ponta a ponta (variante Ruby).
#
# Espelha o teste-manchete de integridade do original (test_node_failure.py):
# gera um arquivo aleatório, calcula seu hash SHA-256, faz o PUT, faz o GET e
# compara os hashes. Prova a correção do ciclo completo de escrita e leitura
# (fatiamento no ingress, replicação com quórum e remontagem no egress).
#
# Requer o cluster no ar (ruby run_cluster.rb).
# Uso: ruby test_integrity.rb [tamanho_MB]
# =============================================================================

require 'digest'
require 'securerandom'
require 'tmpdir'
require_relative 'config'
require_relative 'client'

$stdout.sync = true
mb = (ARGV[0] || 4).to_i
cli = DFS::Client.new
tmp = Dir.mktmpdir('dfs_integ')
src = File.join(tmp, 'orig.bin')
dst = File.join(tmp, 'baixado.bin')
dfs_path = '/teste/integridade.bin'

File.binwrite(src, SecureRandom.random_bytes(mb * 1024 * 1024))
sha_src = Digest::SHA256.file(src).hexdigest
puts "arquivo de #{mb} MB, sha256=#{sha_src[0, 16]}..."

cli.put(src, dfs_path)
cli.get(dfs_path, dst)
sha_dst = Digest::SHA256.file(dst).hexdigest
cli.rm(dfs_path)
FileUtils.rm_rf(tmp)

if sha_src == sha_dst
  puts 'INTEGRIDADE OK: o arquivo baixado é idêntico ao enviado (byte a byte).'
  exit 0
else
  puts "FALHA DE INTEGRIDADE: #{sha_src} != #{sha_dst}"
  exit 1
end
