#!/usr/bin/env bash
# =============================================================================
# test_integrity.sh — Teste de integridade ponta a ponta (variante shellscript).
#
# Espelha o teste-manchete de integridade do original (test_node_failure.py):
# gera um arquivo aleatório, faz o PUT (via ingress), faz o GET (via egress) e
# compara byte a byte. Prova a correção do ciclo completo de escrita e leitura.
#
# Requer o cluster no ar (bash run_cluster.sh).
# Uso: test_integrity.sh [tamanho_MB]
# =============================================================================
set -uo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# No Git Bash/MSYS, impede a conversão dos CAMINHOS LÓGICOS do DFS (ex.: /teste/x)
# em caminhos do Windows. Em POSIX é apenas uma variável sem efeito.
export MSYS_NO_PATHCONV=1

MB="${1:-4}"
TMP="$BASE/.tmp_integ"; mkdir -p "$TMP"   # dir local (evita quirks do /tmp no MSYS)
SRC="$TMP/orig.bin"; DST="$TMP/baixado.bin"; DPATH="/teste/integridade.bin"

head -c $((MB * 1024 * 1024)) /dev/urandom > "$SRC"
echo "arquivo de $MB MB"
bash "$BASE/client.sh" put "$SRC" "$DPATH"
bash "$BASE/client.sh" get "$DPATH" "$DST"
bash "$BASE/client.sh" rm "$DPATH" >/dev/null

if cmp -s "$SRC" "$DST"; then
  echo "INTEGRIDADE OK: o arquivo baixado e identico ao enviado (byte a byte)."
  rm -rf "$TMP"; exit 0
else
  echo "FALHA DE INTEGRIDADE"
  rm -rf "$TMP"; exit 1
fi
