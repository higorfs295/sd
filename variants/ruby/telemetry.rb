# frozen_string_literal: true

# =============================================================================
# telemetry.rb — Hub de telemetria em tempo real (variante Ruby).
#
# Espelha o telemetry_hub.py do original. Lá, um consumidor Kafka escutava o
# tópico de métricas; aqui, sem broker, o monitor consulta periodicamente o
# coordenador (op METRICS), que agrega as métricas reportadas pelos nós
# ingress/egress, e exibe estatísticas ao vivo (mín/máx/média por operação, além
# de contadores de re-replicação e de coleta de lixo).
#
# Uso: ruby telemetry.rb   (Ctrl+C para sair)
# =============================================================================

require_relative 'config'
require_relative 'lib/protocol'

$stdout.sync = true
puts 'Hub de telemetria (Ctrl+C para sair). Consultando o coordenador a cada 1s...'
loop do
  begin
    r = DFS::Protocol.request(DFS::Config::COORDINATOR_HOST, DFS::Config::COORDINATOR_PORT, 'op' => 'METRICS')
    ts = Time.now.strftime('%H:%M:%S')
    line = "[#{ts}] arquivos=#{r['files']} re-replicações=#{r['rereplications']} GC=#{r['gc_deletes']}"
    (r['ops'] || {}).each do |op, m|
      line += " | #{op}: n=#{m['count']} avg=#{m['avg_ms']}ms min=#{m['min_ms']}ms max=#{m['max_ms']}ms #{m['bytes']}B"
    end
    puts line
  rescue StandardError => e
    puts "[telemetria] coordenador indisponível: #{e.message}"
  end
  sleep 1
end
