#!/usr/bin/env bash
# =============================================================================
# client.sh — Interface de linha de comando (CLI) da variante shellscript.
#
# Espelha cli.py + client.py. Cliente "fraco": fala controle com o coordenador
# e dados com os nós. Comandos: put, get, list, rm, status.
#
# PUT: REQUEST_UPLOAD -> envia cada chunk ao nó primary (gateway, fan-out com
#      quórum) -> CONFIRM_UPLOAD.
# GET (estilo GFS): pede o mapa de chunks e busca cada pedaço numa réplica viva.
#
# Uso: client.sh <put|get|list|rm|status> ...
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

  local chunk_size; chunk_size="$(field_of "$plan" chunk_size)"
  local confirm_lines="" _ idx cid reps
  while read -r _ idx cid reps; do
    local primary="${reps%%,*}"
    local spool="$SPOOL/put_${cid}_$$"
    dd if="$local_path" bs="$chunk_size" skip="$idx" count=1 of="$spool" 2>/dev/null

    local resp; resp="$(printf 'op=STORE\nchunk_id=%s\nprimary=1\nfanout=%s\ndata=%s\n' "$cid" "$reps" "$spool" | dfs_rpc "$primary")"
    rm -f "$spool"
    [ "$(field_of "$resp" status)" = "OK" ] || { echo "falha ao gravar chunk $idx: $(field_of "$resp" error)" >&2; exit 1; }

    local stored; stored="$(field_of "$resp" stored)"
    local actual="" r
    IFS=',' read -ra rr <<< "$reps"
    for r in "${rr[@]}"; do [[ ",$stored," == *",$r,"* ]] && actual="${actual:+$actual,}$r"; done
    confirm_lines+="CHUNK $idx $cid $actual"$'\n'
    echo "  chunk $idx: gravado em $stored"
  done < <(printf '%s' "$plan" | grep '^CHUNK ')

  printf 'op=CONFIRM_UPLOAD\npath=%s\nchunk_size=%s\n%s' "$dfs_path" "$chunk_size" "$confirm_lines" | dfs_rpc coordinator >/dev/null
  local nchunks; nchunks="$(printf '%s' "$plan" | grep -c '^CHUNK ')"
  echo "OK: $local_path -> $dfs_path ($nchunks chunk(s))"
}

cmd_get() {
  local dfs_path="$1" local_path="$2"
  local plan; plan="$(printf 'op=REQUEST_DOWNLOAD\npath=%s\n' "$dfs_path" | dfs_rpc coordinator)"
  [ "$(field_of "$plan" status)" = "OK" ] || { echo "coordenador: $(field_of "$plan" error)" >&2; exit 1; }

  : > "$local_path"
  local _ idx cid reps
  while read -r _ idx cid reps; do
    local got=0 r
    IFS=',' read -ra rr <<< "$reps"
    for r in "${rr[@]}"; do
      local resp; resp="$(printf 'op=FETCH\nchunk_id=%s\n' "$cid" | dfs_rpc "$r")"
      if [ "$(field_of "$resp" status)" = "OK" ]; then
        local dp; dp="$(field_of "$resp" data)"
        cat "$dp" >> "$local_path"; rm -f "$dp"; got=1; break
      fi
    done
    [ "$got" -eq 0 ] && { echo "nao consegui obter o chunk $idx de nenhuma replica viva" >&2; exit 1; }
  done < <(printf '%s' "$plan" | grep '^CHUNK ' | sort -k2 -n)
  echo "OK: $dfs_path -> $local_path"
}

cmd_list() {
  local resp; resp="$(printf 'op=LIST_FILES\n' | dfs_rpc coordinator)"
  local files; files="$(printf '%s\n' "$resp" | grep '^FILE ')"
  if [ -z "$files" ]; then echo "(nenhum arquivo)"; return; fi
  printf '%-30s %6s  %s\n' "CAMINHO" "CHUNKS" "NOS"
  local _ path nc nodes
  while read -r _ path nc nodes; do printf '%-30s %6s  %s\n' "$path" "$nc" "$nodes"; done <<< "$files"
}

cmd_rm() {
  local dfs_path="$1"
  local resp; resp="$(printf 'op=DELETE_FILE\npath=%s\n' "$dfs_path" | dfs_rpc coordinator)"
  [ "$(field_of "$resp" status)" = "OK" ] || { echo "erro: $(field_of "$resp" error)" >&2; exit 1; }
  echo "removido: $dfs_path"
}

cmd_status() {
  local resp; resp="$(printf 'op=STATUS\n' | dfs_rpc coordinator)"
  echo "arquivos: $(field_of "$resp" files)"
  local _ n st
  while read -r _ n st; do printf '  %-8s %s\n' "$n" "$st"; done < <(printf '%s' "$resp" | grep '^NODE ')
}

case "${1:-}" in
  put)    cmd_put "$2" "$3" ;;
  get)    cmd_get "$2" "$3" ;;
  list)   cmd_list ;;
  rm)     cmd_rm "$2" ;;
  status) cmd_status ;;
  *) echo "uso: client.sh <put|get|list|rm|status> ..." >&2; exit 1 ;;
esac
