#!/usr/bin/env bash
# =============================================================================
# run_cluster.sh — Orquestrador do cluster (variante shellscript).
#
# Espelha run_cluster.py: sobe o coordenador e os N nós como PROCESSOS bash
# independentes, cada um com sua caixa-postal e seu diretório de chunks.
# Ctrl+C encerra tudo e limpa as caixas-postais.
# =============================================================================
set -uo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BASE/config.sh"

# Limpa estado anterior (caixas-postais e spool) para um começo limpo.
rm -rf "$RUN"
mkdir -p "$RUN" "$SPOOL"

PIDS=()
cleanup() {
  echo ""
  echo "[run_cluster] encerrando cluster..."
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  # Em Cygwin/MSYS, encerra os nós (e seus laços de heartbeat) pelos winpids
  # registrados, com taskkill //T (mata a árvore de processos).
  if [ -d "$RUN/pids" ]; then
    local pf wp
    for pf in "$RUN/pids"/*.pid; do
      [ -f "$pf" ] || continue
      wp="$(cat "$pf")"
      taskkill //F //T //PID "$wp" >/dev/null 2>&1 || kill "$wp" 2>/dev/null || true
    done
  fi
}
trap cleanup EXIT INT TERM

echo "[run_cluster] subindo o coordenador..."
bash "$BASE/coordinator.sh" &
PIDS+=($!)
sleep 1.5

for node in $(node_list); do
  echo "[run_cluster] subindo $node..."
  bash "$BASE/node.sh" "$node" &
  PIDS+=($!)
  sleep 0.3
done

echo "[run_cluster] ecossistema DFS operacional. Ctrl+C para encerrar."
echo "[run_cluster] em outro terminal:  bash client.sh put <arquivo> /destino"
wait
