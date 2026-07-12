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

# Telemetria: reporta a duração (ms) e o volume de uma operação ao coordenador.
emit_metric() {
  local metric="$1" start="$2" bytes="$3" dur_ms
  dur_ms="$(awk "BEGIN{printf \"%d\", ($EPOCHREALTIME - $start) * 1000}")"
  printf 'op=METRIC\nmetric=%s\nduration_ms=%s\nbytes=%s\n' "$metric" "$dur_ms" "$bytes" | dfs_rpc coordinator >/dev/null 2>&1 || true
}

# ---- Handler das operações do plano de dados --------------------------------
node_handle() {
  local req="$1" reqid="$2" server="$3" op
  dfs_field "$req" op; op="$FIELD"

  case "$op" in
    PING)
      echo "status=OK"; echo "node_id=$NODE_ID" ;;

    UPLOAD_FILE)
      # Papel de INGRESS: recebe o arquivo inteiro + o plano, fatia, grava/replica
      # com quórum e confirma ao coordenador. Espelha DataServicer.UploadFile.
      local path upload_id chunk_size datapath start="$EPOCHREALTIME"
      dfs_field "$req" path; path="$FIELD"
      dfs_field "$req" upload_id; upload_id="$FIELD"
      dfs_field "$req" chunk_size; chunk_size="$FIELD"
      dfs_field "$req" data; datapath="$FIELD"
      local total; total="$(wc -c < "$datapath")"
      local conf="" failed="" _ idx cid reps
      while read -r _ idx cid reps; do
        local tmpc="$SPOOL/up_${reqid}_${idx}"
        dd if="$datapath" bs="$chunk_size" skip="$idx" count=1 of="$tmpc" 2>/dev/null
        local self_rep=0 r
        IFS=',' read -ra rr <<< "$reps"
        for r in "${rr[@]}"; do [ "$r" = "$NODE_ID" ] && self_rep=1; done
        [ "$self_rep" = 1 ] && cp "$tmpc" "$CHUNKS/$cid"
        local actual="" count=0 resp
        for r in "${rr[@]}"; do
          [ -z "$r" ] && continue
          if [ "$r" = "$NODE_ID" ]; then
            [ "$self_rep" = 1 ] && { actual="${actual:+$actual,}$r"; count=$((count + 1)); }
          else
            resp="$(printf 'op=STORE\nchunk_id=%s\nprimary=0\ndata=%s\n' "$cid" "$tmpc" | dfs_rpc "$r")"
            dfs_field_str "$resp" status
            [ "$FIELD" = "OK" ] && { actual="${actual:+$actual,}$r"; count=$((count + 1)); } || nlog "fan-out falhou p/ $r"
          fi
        done
        rm -f "$tmpc"
        local q=$WRITE_QUORUM; [ "${#rr[@]}" -lt "$q" ] && q=${#rr[@]}
        if [ "$count" -lt "$q" ]; then failed="$idx"; break; fi
        conf+="CHUNK $idx $cid $actual"$'\n'
      done < <(grep '^CHUNK ' "$req")

      if [ -n "$failed" ]; then echo "status=ERR"; echo "error=quorum nao atingido no chunk $failed"; return; fi
      # O INGRESS confirma ao coordenador (cliente fraco não confirma).
      { printf 'op=CONFIRM_UPLOAD\npath=%s\nchunk_size=%s\nsize=%s\ningress=%s\n' "$path" "$chunk_size" "$total" "$NODE_ID"
        printf '%s' "$conf"; } | dfs_rpc coordinator >/dev/null
      emit_metric upload "$start" "$total"
      local nch; nch="$(grep -c '^CHUNK ' "$req")"
      nlog "ingress: $path ($nch chunk(s), $total B) confirmado"
      echo "status=OK"; echo "chunks_written=$nch"; echo "bytes=$total" ;;

    DOWNLOAD_FILE)
      # Papel de EGRESS: reúne os chunks (locais + buscados em peers) e devolve o
      # arquivo montado. Espelha DataServicer.DownloadFile.
      local start="$EPOCHREALTIME" outp="$RUN/$server/out/$reqid.resp.data"
      : > "$outp"
      local _ idx cid reps missing="" r
      while read -r _ idx cid reps; do
        if [ -f "$CHUNKS/$cid" ]; then
          cat "$CHUNKS/$cid" >> "$outp"
        else
          local got=0 resp dp
          IFS=',' read -ra rr <<< "$reps"
          for r in "${rr[@]}"; do
            [ "$r" = "$NODE_ID" ] && continue
            resp="$(printf 'op=FETCH\nchunk_id=%s\n' "$cid" | dfs_rpc "$r")"
            dfs_field_str "$resp" status
            if [ "$FIELD" = "OK" ]; then dfs_field_str "$resp" data; dp="$FIELD"; cat "$dp" >> "$outp"; got=1; break; fi
          done
          [ "$got" = 0 ] && { missing="$idx"; break; }
        fi
      done < <(grep '^CHUNK ' "$req")

      if [ -n "$missing" ]; then echo "status=ERR"; echo "error=chunk $missing indisponivel"; return; fi
      local total; total="$(wc -c < "$outp")"
      emit_metric download "$start" "$total"
      nlog "egress: servindo arquivo ($total B)"
      echo "status=OK"; echo "data=$outp"; echo "bytes=$total" ;;

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
