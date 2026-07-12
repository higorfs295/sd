#!/usr/bin/env bash
# =============================================================================
# telemetry.sh — Hub de telemetria em tempo real (variante shellscript).
#
# Espelha o telemetry_hub.py. Sem broker Kafka, consulta o coordenador (op
# METRICS) a cada segundo e exibe as estatísticas ao vivo (mín/máx/média por
# operação, além de re-replicações e coleta de lixo).
#
# Uso: telemetry.sh   (Ctrl+C para sair)
# =============================================================================
set -uo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BASE/config.sh"
source "$BASE/lib.sh"

field_of() { printf '%s' "$1" | sed -n "s/^$2=//p" | head -1; }

echo "Hub de telemetria (Ctrl+C para sair). Consultando o coordenador a cada 1s..."
while true; do
  resp="$(printf 'op=METRICS\n' | dfs_rpc coordinator)"
  ts="$(date +%H:%M:%S)"
  line="[$ts] arquivos=$(field_of "$resp" files) re-replicacoes=$(field_of "$resp" rereplications) GC=$(field_of "$resp" gc_deletes)"
  while read -r _ op c avg mn mx by; do
    [ -z "${op:-}" ] && continue
    line+=" | $op: n=$c avg=${avg}ms min=${mn}ms max=${mx}ms ${by}B"
  done < <(printf '%s\n' "$resp" | grep '^OP ')
  echo "$line"
  sleep 1
done
