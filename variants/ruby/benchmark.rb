# frozen_string_literal: true

# =============================================================================
# benchmark.rb — Arcabouço de benchmark de carga (variante Ruby).
#
# Espelha benchmark_harness.py + plot_metrics.py do original (aqui sem gráficos:
# grava CSV e imprime a tabela). Para cada tamanho de arquivo, roda N iterações
# de PUT e GET, medindo latência (ms) e throughput (MB/s), e grava os resultados
# em benchmark/resultados.csv. Cobre a "análise experimental" da especificação.
#
# Uso: ruby benchmark.rb [--sizes 1 2 5 10] [--iter 3]   (tamanhos em MB)
# =============================================================================

require 'securerandom'
require 'fileutils'
require_relative 'config'
require_relative 'client'

$stdout.sync = true

sizes = [1, 2, 5]
iter = 3
if (i = ARGV.index('--sizes'))
  sizes = []
  j = i + 1
  while j < ARGV.size && ARGV[j] =~ /\A\d+\z/
    sizes << ARGV[j].to_i
    j += 1
  end
end
iter = ARGV[ARGV.index('--iter') + 1].to_i if ARGV.include?('--iter')

cli = DFS::Client.new
tmp = File.join(Dir.tmpdir, "dfs_bench_#{Process.pid}")
FileUtils.mkdir_p(tmp)
out_dir = File.join(__dir__, 'benchmark')
FileUtils.mkdir_p(out_dir)
csv = File.join(out_dir, 'resultados.csv')

rows = []
puts format('%-8s %-6s %-4s %12s %12s', 'OP', 'MB', 'IT', 'LATENCIA_ms', 'THRPUT_MBps')
sizes.each do |mb|
  src = File.join(tmp, "f#{mb}.bin")
  File.binwrite(src, SecureRandom.random_bytes(mb * 1024 * 1024))
  dst = File.join(tmp, "g#{mb}.bin")
  dfs_path = "/bench/f#{mb}.bin"

  (1..iter).each do |it|
    t = Time.now
    silent { cli.put(src, dfs_path) }
    put_ms = (Time.now - t) * 1000
    put_mbps = mb / (put_ms / 1000.0)
    rows << ['put', mb, it, put_ms.round(2), put_mbps.round(2)]
    puts format('%-8s %-6d %-4d %12.2f %12.2f', 'put', mb, it, put_ms, put_mbps)

    t = Time.now
    silent { cli.get(dfs_path, dst) }
    get_ms = (Time.now - t) * 1000
    get_mbps = mb / (get_ms / 1000.0)
    rows << ['get', mb, it, get_ms.round(2), get_mbps.round(2)]
    puts format('%-8s %-6d %-4d %12.2f %12.2f', 'get', mb, it, get_ms, get_mbps)
  end
  silent { cli.rm(dfs_path) }
end

File.open(csv, 'w') do |f|
  f.puts 'op,size_mb,iter,latency_ms,throughput_mbps'
  rows.each { |r| f.puts r.join(',') }
end
FileUtils.rm_rf(tmp)
puts "\nCSV gravado em #{csv}"

BEGIN {
  # silencia o stdout de um bloco (as CLIs imprimem "OK: ...").
  def silent
    orig = $stdout
    $stdout = File.open(File::NULL, 'w')
    yield
  ensure
    $stdout.close
    $stdout = orig
  end
  require 'tmpdir'
}
