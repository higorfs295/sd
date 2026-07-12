#!/usr/bin/env bash
# =============================================================================
# test_unit.sh — Testes de unidade das funções puras (variante shellscript).
#
# Espelha test_chunking.py + a validação de placement do original. Não precisa
# do cluster: exercita a regra de posicionamento determinístico (round-robin) e
# o dimensionamento adaptável de chunk.
#
# Uso: test_unit.sh
# =============================================================================
set -uo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BASE/config.sh"
source "$BASE/lib.sh"

FAILURES=0
check() { if eval "$2"; then echo "ok   - $1"; else echo "FALHA - $1"; FAILURES=$((FAILURES + 1)); fi; }

# Placement determinístico: chunk i -> nós i, i+1, i+2 (mod N), sobre 5 nós.
check "chunk 0 -> node1 node2 node3" '[ "$(dfs_replicas 0 5 | xargs)" = "node1 node2 node3" ]'
check "chunk 3 da a volta -> node4 node5 node1" '[ "$(dfs_replicas 3 5 | xargs)" = "node4 node5 node1" ]'
check "determinismo: mesma entrada, mesma saida" '[ "$(dfs_replicas 7 5)" = "$(dfs_replicas 7 5)" ]'

# Dimensionamento de chunk.
check "arquivo pequeno -> piso MIN_CHUNK_SIZE" '[ "$(dfs_chunk_size 1000 5)" = "$MIN_CHUNK_SIZE" ]'
check "chunk nunca abaixo do piso" '[ "$(dfs_chunk_size $((50 * 1024 * 1024)) 5)" -ge "$MIN_CHUNK_SIZE" ]'
check "chunk nunca acima do teto" '[ "$(dfs_chunk_size $((300 * 1024 * 1024)) 5)" -le "$MAX_CHUNK_SIZE" ]'

if [ "$FAILURES" -eq 0 ]; then echo ""; echo "TODOS OS TESTES PASSARAM"; exit 0
else echo ""; echo "$FAILURES TESTE(S) FALHARAM"; exit 1; fi
