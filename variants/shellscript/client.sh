#!/usr/bin/env bash
# =============================================================================
# client.sh — Interface de linha de comando (CLI) da variante shellscript.
#
# Espelha cli.py + client.py. Cliente "fraco": fala controle com o coordenador
# e dados com os nós. Comandos: put, get, list, rm, status.
#
# CLIENTE FRACO: não fatia arquivos nem decide posicionamento.
# PUT: REQUEST_UPLOAD -> entrega o arquivo INTEIRO ao INGRESS (que fatia, replica
#      com quórum e confirma ao coordenador).
# GET: REQUEST_DOWNLOAD -> pede o arquivo ao EGRESS (que remonta por localidade).
#
# Uso: client.sh <put|get|list|rm|status|metrics> ...
# =============================================================================
set -uo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BASE/config.sh"
source "$BASE/lib.sh"

field_of() { printf '%s' "$1" | sed -n "s/^$2=//p" | head -1; }

cmd_put() {
  local local_path="$1" dfs_path="$2"
  [ -f "$local_path" ] || { echo "arquivo local nao existe: $local_path" >&2; exit 1; }
  local size; size="$(stat -c %s "$local_path")"

  local plan; plan="$(printf 'op=REQUEST_UPLOAD\npath=%s\nsize=%s\n' "$dfs_path" "$size" | dfs_rpc coordinator)"
  [ "$(field_of "$plan" status)" = "OK" ] || { echo "coordenador recusou: $(field_of "$plan" error)" >&2; exit 1; }

  local ingress upload_id chunk_size
  ingress="$(field_of "$plan" ingress)"
  upload_id="$(field_of "$plan" upload_id)"
  chunk_size="$(field_of "$plan" chunk_size)"

  # Entrega o arquivo INTEIRO ao ingress (via spool compartilhado, caminho absoluto).
  local spool="$SPOOL/upload_$$_${upload_id}"
  cp "$local_path" "$spool"
  local resp; resp="$( { printf 'op=UPLOAD_FILE\npath=%s\nupload_id=%s\nchunk_size=%s\ndata=%s\n' "$dfs_path" "$upload_id" "$chunk_size" "$spool"
                         printf '%s\n' "$plan" | grep '^CHUNK '; } | dfs_rpc "$ingress" )"
  rm -f "$spool"
  [ "$(field_of "$resp" status)" = "OK" ] || { echo "falha no upload (ingress $ingress): $(field_of "$resp" error)" >&2; exit 1; }
  echo "OK: $local_path -> $dfs_path via ingress $ingress ($(field_of "$resp" chunks_written) chunk(s), $(field_of "$resp" bytes) B)"
}

cmd_get() {
  local dfs_path="$1" local_path="$2"
  local plan; plan="$(printf 'op=REQUEST_DOWNLOAD\npath=%s\n' "$dfs_path" | dfs_rpc coordinator)"
  [ "$(field_of "$plan" status)" = "OK" ] || { echo "coordenador: $(field_of "$plan" error)" >&2; exit 1; }

  local egress; egress="$(field_of "$plan" egress)"
  local resp; resp="$( { printf 'op=DOWNLOAD_FILE\npath=%s\n' "$dfs_path"
                         printf '%s\n' "$plan" | grep '^CHUNK '; } | dfs_rpc "$egress" )"
  [ "$(field_of "$resp" status)" = "OK" ] || { echo "falha no download (egress $egress): $(field_of "$resp" error)" >&2; exit 1; }
  local dp; dp="$(field_of "$resp" data)"
  cp "$dp" "$local_path"; rm -f "$dp"
  echo "OK: $dfs_path -> $local_path via egress $egress ($(field_of "$resp" bytes) B)"
}

cmd_list() {
  local resp; resp="$(printf 'op=LIST_FILES\n' | dfs_rpc coordinator)"
  local files; files="$(printf '%s\n' "$resp" | grep '^FILE ')"
  if [ -z "$files" ]; then echo "(nenhum arquivo)"; return; fi
  printf '%-28s %6s %10s  %s\n' "CAMINHO" "CHUNKS" "BYTES" "NOS"
  local _ path nc sz nodes
  while read -r _ path nc sz nodes; do printf '%-28s %6s %10s  %s\n' "$path" "$nc" "$sz" "$nodes"; done <<< "$files"
}

cmd_rm() {
  local dfs_path="$1"
  local resp; resp="$(printf 'op=DELETE_FILE\npath=%s\n' "$dfs_path" | dfs_rpc coordinator)"
  [ "$(field_of "$resp" status)" = "OK" ] || { echo "erro: $(field_of "$resp" error)" >&2; exit 1; }
  echo "removido: $dfs_path"
}

cmd_status() {
  local resp; resp="$(printf 'op=STATUS\n' | dfs_rpc coordinator)"
  echo "arquivos: $(field_of "$resp" files) | re-replicacoes: $(field_of "$resp" rereplications) | GC: $(field_of "$resp" gc_deletes)"
  local _ n st
  while read -r _ n st; do printf '  %-8s %s\n' "$n" "$st"; done < <(printf '%s' "$resp" | grep '^NODE ')
}

cmd_metrics() {
  local resp; resp="$(printf 'op=METRICS\n' | dfs_rpc coordinator)"
  echo "arquivos: $(field_of "$resp" files) | re-replicacoes: $(field_of "$resp" rereplications) | GC apagou: $(field_of "$resp" gc_deletes)"
  local ops; ops="$(printf '%s\n' "$resp" | grep '^OP ')"
  if [ -z "$ops" ]; then echo "(sem metricas de operacao ainda)"; return; fi
  printf '%-10s %6s %10s %10s %10s %12s\n' "OP" "N" "AVG(ms)" "MIN(ms)" "MAX(ms)" "BYTES"
  local _ op c avg mn mx by
  while read -r _ op c avg mn mx by; do printf '%-10s %6s %10s %10s %10s %12s\n' "$op" "$c" "$avg" "$mn" "$mx" "$by"; done <<< "$ops"
}

case "${1:-}" in
  put)     cmd_put "$2" "$3" ;;
  get)     cmd_get "$2" "$3" ;;
  list)    cmd_list ;;
  rm)      cmd_rm "$2" ;;
  status)  cmd_status ;;
  metrics) cmd_metrics ;;
  *) echo "uso: client.sh <put|get|list|rm|status|metrics> ..." >&2; exit 1 ;;
esac
