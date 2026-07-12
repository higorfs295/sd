#!/usr/bin/env bash
# =============================================================================
# benchmark.sh — Arcabouço de benchmark de carga (variante shellscript).
#
# Espelha benchmark_harness.py + plot_metrics.py (aqui sem gráficos: grava CSV e
# imprime a tabela). Para cada tamanho de arquivo, roda N iterações de PUT e GET
# medindo latência (ms) e throughput (MB/s), e grava benchmark/resultados.csv.
#
# Uso: benchmark.sh [--sizes 1 2 5] [--iter 3]   (tamanhos em MB)
# =============================================================================
set -uo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# No Git Bash/MSYS, impede a conversão dos CAMINHOS LÓGICOS do DFS em caminhos
# do Windows. Em POSIX é apenas uma variável sem efeito.
export MSYS_NO_PATHCONV=1
source "$BASE/config.sh"

SIZES=(1 2 5); ITER=3
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in
    --iter) ITER="${args[$((i + 1))]}" ;;
    --sizes) SIZES=(); j=$((i + 1)); while [ $j -lt ${#args[@]} ] && [[ "${args[$j]}" =~ ^[0-9]+$ ]]; do SIZES+=("${args[$j]}"); j=$((j + 1)); done ;;
  esac
done

mkdir -p "$BASE/benchmark"
CSV="$BASE/benchmark/resultados.csv"
echo "op,size_mb,iter,latency_ms,throughput_mbps" > "$CSV"
printf '%-8s %-6s %-4s %12s %12s\n' "OP" "MB" "IT" "LATENCIA_ms" "THRPUT_MBps"

TMP="$BASE/.tmp_bench"; mkdir -p "$TMP"   # dir local (evita quirks do /tmp no MSYS)
ms() { awk "BEGIN{printf \"%.2f\", ($2 - $1) * 1000}"; }

for mb in "${SIZES[@]}"; do
  src="$TMP/f${mb}.bin"; dst="$TMP/g${mb}.bin"; dpath="/bench/f${mb}.bin"
  head -c $((mb * 1024 * 1024)) /dev/urandom > "$src"
  for ((it = 1; it <= ITER; it++)); do
    t0="$EPOCHREALTIME"; bash "$BASE/client.sh" put "$src" "$dpath" >/dev/null; t1="$EPOCHREALTIME"
    put_ms="$(ms "$t0" "$t1")"; put_mbps="$(awk "BEGIN{printf \"%.2f\", $mb / ($put_ms/1000)}")"
    printf '%-8s %-6s %-4s %12s %12s\n' "put" "$mb" "$it" "$put_ms" "$put_mbps"
    echo "put,$mb,$it,$put_ms,$put_mbps" >> "$CSV"

    t0="$EPOCHREALTIME"; bash "$BASE/client.sh" get "$dpath" "$dst" >/dev/null; t1="$EPOCHREALTIME"
    get_ms="$(ms "$t0" "$t1")"; get_mbps="$(awk "BEGIN{printf \"%.2f\", $mb / ($get_ms/1000)}")"
    printf '%-8s %-6s %-4s %12s %12s\n' "get" "$mb" "$it" "$get_ms" "$get_mbps"
    echo "get,$mb,$it,$get_ms,$get_mbps" >> "$CSV"
  done
  bash "$BASE/client.sh" rm "$dpath" >/dev/null
done
rm -rf "$TMP"
echo ""
echo "CSV gravado em $CSV"
