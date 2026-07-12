#!/usr/bin/env bash
# run_cluster.sh — Orquestrador do cluster (variante C).
#
# Espelha run_cluster.py: sobe o coordenador e os N nós como PROCESSOS
# independentes (cada um com seu servidor TCP e seu diretório em disco).
# Ctrl+C encerra tudo.
set -e
cd "$(dirname "$0")"

EXT=""
case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) EXT=".exe" ;; esac

if [ ! -x "./coordinator$EXT" ]; then
  echo "[run_cluster] binários não encontrados; compilando..."
  ./build.sh
fi

PIDS=()
cleanup() {
  echo ""
  echo "[run_cluster] encerrando cluster..."
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT INT TERM

echo "[run_cluster] subindo o coordenador..."
"./coordinator$EXT" &
PIDS+=($!)
sleep 2

N=5
for i in $(seq 1 $N); do
  port=$((9100 + i))
  echo "[run_cluster] subindo node$i na porta $port..."
  "./node$EXT" "node$i" "$port" "data/nodes/node$i" &
  PIDS+=($!)
  sleep 0.3
done

echo "[run_cluster] ecossistema DFS operacional. Ctrl+C para encerrar."
echo "[run_cluster] em outro terminal:  ./client$EXT put <arquivo> /destino"
wait
