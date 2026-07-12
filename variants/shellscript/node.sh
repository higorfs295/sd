#!/usr/bin/env bash
# =============================================================================
# node.sh — Nó de armazenamento (plano de dados) da variante shellscript.
#
# Espelha storage_node.py + data_service.py + local_storage.py. Papéis:
# armazenador, réplica, gateway/primary (fan-out com quórum) e emissor de
# heartbeat com block report. Recebe do coordenador a lista de órfãos (GC).
#
# Uso: node.sh <node_id>
# =============================================================================
set -uo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BASE/config.sh"
source "$BASE/lib.sh"

NODE_ID="${1:?uso: node.sh <node_id>}"
CHUNKS="$(node_chunks_dir "$NODE_ID")"
mkdir -p "$CHUNKS"
dfs_init_mailbox "$NODE_ID"

# Registra o PID (Windows, quando em Cygwin/MSYS) para permitir encerrar o nó de
# forma confiável nos testes de falha. Em POSIX grava o PID normal.
mkdir -p "$RUN/pids"
if [ -r "/proc/$$/winpid" ]; then cat "/proc/$$/winpid" > "$RUN/pids/$NODE_ID.pid"; else echo "$$" > "$RUN/pids/$NODE_ID.pid"; fi

nlog() { dfs_log "$NODE_ID" "$1"; }

# Lista os chunks locais como CSV (block report) -> global LC (bash puro).
LC=""
local_chunks_csv() {
  local x out=""
  shopt -s nullglob
  for x in "$CHUNKS"/*; do out="${out:+$out,}${x##*/}"; done
  LC="$out"
}

# ---- Handler das operações do plano de dados --------------------------------
node_handle() {
  local req="$1" reqid="$2" server="$3" op
  dfs_field "$req" op; op="$FIELD"

  case "$op" in
    PING)
      echo "status=OK"; echo "node_id=$NODE_ID" ;;

    STORE)
      local chunk_id primary datapath
      dfs_field "$req" chunk_id; chunk_id="$FIELD"
      dfs_field "$req" primary; primary="$FIELD"
      dfs_field "$req" data; datapath="$FIELD"
      cp "$datapath" "$CHUNKS/$chunk_id"

      if [ "$primary" != "1" ]; then echo "status=OK"; echo "stored=$NODE_ID"; return; fi

      # Papel de primary: fan-out às réplicas, exigindo quórum.
      local fanout stored count rn resp
      dfs_field "$req" fanout; fanout="$FIELD"
      stored="$NODE_ID"; count=1
      IFS=',' read -ra fo <<< "$fanout"
      for rn in "${fo[@]}"; do
        [ -z "$rn" ] && continue
        [ "$rn" = "$NODE_ID" ] && continue
        resp="$(printf 'op=STORE\nchunk_id=%s\nprimary=0\ndata=%s\n' "$chunk_id" "$datapath" | dfs_rpc "$rn")"
        dfs_field_str "$resp" status
        if [ "$FIELD" = "OK" ]; then stored="$stored,$rn"; count=$((count + 1)); else nlog "fan-out falhou p/ $rn"; fi
      done
      if [ "$count" -ge "$WRITE_QUORUM" ]; then echo "status=OK"; else echo "status=ERR"; echo "error=quorum ($count/$WRITE_QUORUM)"; fi
      echo "stored=$stored" ;;

    FETCH)
      local chunk_id; dfs_field "$req" chunk_id; chunk_id="$FIELD"
      if [ -f "$CHUNKS/$chunk_id" ]; then
        cp "$CHUNKS/$chunk_id" "$RUN/$server/out/$reqid.resp.data"
        echo "status=OK"; echo "data=$RUN/$server/out/$reqid.resp.data"
      else
        echo "status=ERR"; echo "error=chunk ausente"
      fi ;;

    DELETE)
      local chunk_id; dfs_field "$req" chunk_id; chunk_id="$FIELD"
      rm -f "$CHUNKS/$chunk_id"; echo "status=OK" ;;

    LIST)
      local_chunks_csv; echo "status=OK"; echo "chunks=$LC" ;;

    REPLICATE)
      local chunk_id target resp
      dfs_field "$req" chunk_id; chunk_id="$FIELD"
      dfs_field "$req" target; target="$FIELD"
      if [ ! -f "$CHUNKS/$chunk_id" ]; then echo "status=ERR"; echo "error=fonte nao tem o chunk"; return; fi
      resp="$(printf 'op=STORE\nchunk_id=%s\nprimary=0\ndata=%s\n' "$chunk_id" "$CHUNKS/$chunk_id" | dfs_rpc "$target")"
      dfs_field_str "$resp" status
      if [ "$FIELD" = "OK" ]; then nlog "re-replicou $chunk_id -> $target"; echo "status=OK"; else echo "status=ERR"; fi ;;

    *)
      echo "status=ERR"; echo "error=op desconhecida: $op" ;;
  esac
}

# ---- Heartbeat + garbage collection -----------------------------------------
heartbeat_loop() {
  dfs_nap_setup
  local i resp del c
  for ((i = 0; i < 10; i++)); do
    resp="$(printf 'op=REGISTER\nnode=%s\n' "$NODE_ID" | dfs_rpc coordinator)"
    [ -n "$resp" ] && { nlog "registrado no coordenador"; break; }
    dfs_nap 1
  done
  while true; do
    dfs_nap "$HEARTBEAT_INTERVAL"
    local_chunks_csv
    resp="$(printf 'op=HEARTBEAT\nnode=%s\nchunks=%s\n' "$NODE_ID" "$LC" | dfs_rpc coordinator)"
    dfs_field_str "$resp" delete; del="$FIELD"
    if [ -n "$del" ]; then
      IFS=',' read -ra ds <<< "$del"
      for c in "${ds[@]}"; do
        [ -z "$c" ] && continue
        if [ -f "$CHUNKS/$c" ]; then rm -f "$CHUNKS/$c"; nlog "GC apagou orfao $c"; fi
      done
    fi
  done
}

heartbeat_loop &
HB_PID=$!
# Ao encerrar o nó (EXIT/TERM/INT), derruba também o laço de heartbeat, para
# que o coordenador realmente deixe de receber batimentos e detecte a MORTE.
trap 'kill "$HB_PID" 2>/dev/null; exit 0' EXIT INT TERM

nlog "no no ar (mailbox=$NODE_ID, dir=$CHUNKS)"
dfs_serve "$NODE_ID" node_handle
