#!/usr/bin/env bash
# =============================================================================
# coordinator.sh — Coordenador (plano de controle) da variante shellscript.
#
# Espelha server.py + node_registry.py + metadata_service.py +
# replication_watcher.py. Mantém:
#   - METADADOS como arquivos (um por arquivo do DFS) em run/coordinator/meta.
#   - REGISTRO DE NÓS com vivacidade (ALIVE/SUSPECT/DEAD) em memória (o
#     coordenador é um único processo servindo requisições em série).
#   - PLACEMENT determinístico, SUPERVISOR DE RE-REPLICAÇÃO (rodado no gancho
#     dfs_on_idle) e GARBAGE COLLECTION por block report.
#
# Como o servidor processa uma requisição por vez, não há concorrência interna
# a proteger: a serialização das mensagens já garante consistência do estado.
# O caminho quente (REGISTER/HEARTBEAT/STATUS) evita forks (crítico em Cygwin).
#
# Uso: coordinator.sh
# =============================================================================
set -uo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$BASE/config.sh"
source "$BASE/lib.sh"

mkdir -p "$META/files"
dfs_init_mailbox coordinator

clog() { dfs_log coordenador "$1"; }

# ---- Estado em memória -------------------------------------------------------
declare -A LAST_HB       # node -> epoch do último heartbeat
declare -A PENDING       # node -> csv de chunk_ids pendentes de deleção
declare -A SUSPECT       # "node|chunk" -> contagem de ciclos como órfão
declare -A PREV_STATE    # node -> estado anterior (p/ detectar transições)
declare -A INFLIGHT      # upload_id -> expiração (protege upload em andamento do GC)
MEMBERSHIP=($(node_list))
for _n in "${MEMBERSHIP[@]}"; do PREV_STATE["$_n"]=DEAD; done
LAST_WATCH=0

# ---- Vivacidade (resultado na global ST, sem fork) --------------------------
ST=""
state_of() {
  local hb="${LAST_HB[$1]:-0}"
  if [ "$hb" = "0" ]; then ST=DEAD; return; fi
  dfs_now
  local silence=$((NOW - hb))
  if   [ "$silence" -lt "$HEARTBEAT_SUSPECT" ]; then ST=ALIVE
  elif [ "$silence" -lt "$HEARTBEAT_DEAD" ];    then ST=SUSPECT
  else ST=DEAD; fi
}
is_dead() { state_of "$1"; [ "$ST" = "DEAD" ]; }

in_membership() { local n; for n in "${MEMBERSHIP[@]}"; do [ "$n" = "$1" ] && return 0; done; return 1; }

# ---- Metadados (arquivos) ----------------------------------------------------
enc_path() { printf '%s' "$1" | base64 | tr '/+' '_-' | tr -d '=\n'; }

# CSV de réplicas de um chunk (procura em todos os arquivos de metadados).
chunk_replicas() {
  grep -h -E "^CHUNK [0-9]+ $1 " "$META/files/"* 2>/dev/null | head -1 | awk '{print $4}'
}
node_expects() { local reps; reps="$(chunk_replicas "$2")"; [[ ",$reps," == *",$1,"* ]]; }

set_chunk_replicas() {
  awk -v cid="$2" -v csv="$3" '$1=="CHUNK" && $3==cid { print "CHUNK", $2, $3, csv; next } { print }' "$1" > "$1.tmp" && mv "$1.tmp" "$1"
}

# ---- Handler das chamadas do plano de controle ------------------------------
coord_handle() {
  local req="$1" reqid="$2" server="$3" op node path size chunks
  dfs_field "$req" op; op="$FIELD"

  case "$op" in
    REGISTER)
      dfs_field "$req" node; node="$FIELD"
      dfs_now; LAST_HB["$node"]="$NOW"
      if ! in_membership "$node"; then MEMBERSHIP+=("$node"); PREV_STATE["$node"]=DEAD; fi
      clog "no $node registrado (membership=${#MEMBERSHIP[@]})"
      echo "status=OK"; echo "cluster_size=${#MEMBERSHIP[@]}" ;;

    HEARTBEAT)
      dfs_field "$req" node; node="$FIELD"
      dfs_field "$req" chunks; chunks="$FIELD"
      dfs_now; LAST_HB["$node"]="$NOW"
      local out_del="${PENDING[$node]:-}"; PENDING["$node"]=""

      # Órfãos por block report, confirmados em 2 ciclos consecutivos.
      if [ -n "$chunks" ]; then
        local -A current_orphans=(); local c
        IFS=',' read -ra cs <<< "$chunks"
        for c in "${cs[@]}"; do
          [ -z "$c" ] && continue
          # Protege chunks de um upload ainda EM ANDAMENTO (antes do CONFIRM).
          local uid="${c%_chunk_*}"
          if [ -n "${INFLIGHT[$uid]:-}" ] && [ "$NOW" -lt "${INFLIGHT[$uid]}" ]; then continue; fi
          if ! node_expects "$node" "$c"; then
            current_orphans["$c"]=1
            local cnt="${SUSPECT[$node|$c]:-0}"; cnt=$((cnt + 1)); SUSPECT["$node|$c"]=$cnt
            [ "$cnt" -ge 2 ] && out_del="${out_del:+$out_del,}$c"
          fi
        done
        local k
        for k in "${!SUSPECT[@]}"; do
          [[ "$k" == "$node|"* ]] || continue
          [ -n "${current_orphans[${k#*|}]:-}" ] || unset 'SUSPECT[$k]'
        done
      fi
      echo "status=OK"; echo "delete=$out_del" ;;

    REQUEST_UPLOAD)
      dfs_field "$req" path; path="$FIELD"
      dfs_field "$req" size; size="$FIELD"
      local n=${#MEMBERSHIP[@]} cs num up i
      cs="$(dfs_chunk_size "$size" "$n")"
      if [ "$size" -le 0 ]; then num=1; else num=$(( (size + cs - 1) / cs )); fi
      [ "$num" -le 0 ] && num=1
      dfs_now; up="up_${NOW}_${RANDOM}"
      INFLIGHT["$up"]=$((NOW + 120))   # janela de proteção do upload contra o GC

      local lines="" bad=""
      for ((i = 0; i < num; i++)); do
        local reps live=0 r csv=""
        reps="$(dfs_replicas "$i" "$n")"
        for r in $reps; do csv="${csv:+$csv,}$r"; is_dead "$r" || live=$((live + 1)); done
        if [ "$live" -lt "$WRITE_QUORUM" ]; then bad="$i"; break; fi
        lines+="CHUNK $i ${up}_chunk_${i} $csv"$'\n'
      done
      if [ -n "$bad" ]; then echo "status=ERR"; echo "error=replicas vivas insuficientes p/ quorum no chunk $bad"; return; fi
      clog "upload $up p/ $path: $num chunk(s) de $cs B"
      echo "status=OK"; echo "upload_id=$up"; echo "chunk_size=$cs"; echo "num_chunks=$num"
      printf '%s' "$lines" ;;

    CONFIRM_UPLOAD)
      dfs_field "$req" path; path="$FIELD"
      dfs_field "$req" chunk_size; local cs="$FIELD"
      local mf="$META/files/$(enc_path "$path")"
      {
        echo "path=$path"
        echo "num_chunks=$(grep -c '^CHUNK ' "$req")"
        echo "chunk_size=$cs"
        grep '^CHUNK ' "$req"
      } > "$mf"
      # Upload concluído: libera a proteção contra o GC.
      local firstc uid; firstc="$(grep -m1 '^CHUNK ' "$req" | awk '{print $3}')"; uid="${firstc%_chunk_*}"
      [ -n "$uid" ] && unset 'INFLIGHT[$uid]'
      clog "arquivo $path confirmado nos metadados"
      echo "status=OK" ;;

    REQUEST_DOWNLOAD)
      dfs_field "$req" path; path="$FIELD"
      local mf="$META/files/$(enc_path "$path")"
      if [ ! -f "$mf" ]; then echo "status=ERR"; echo "error=arquivo nao encontrado"; return; fi
      dfs_field "$mf" num_chunks; echo "status=OK"; echo "num_chunks=$FIELD"
      local _ idx cid reps
      while read -r _ idx cid reps; do
        local live="" r
        IFS=',' read -ra rr <<< "$reps"
        for r in "${rr[@]}"; do is_dead "$r" || live="${live:+$live,}$r"; done
        [ -z "$live" ] && live="$reps"
        echo "CHUNK $idx $cid $live"
      done < <(grep '^CHUNK ' "$mf") ;;

    DELETE_FILE)
      dfs_field "$req" path; path="$FIELD"
      local mf="$META/files/$(enc_path "$path")"
      if [ ! -f "$mf" ]; then echo "status=ERR"; echo "error=arquivo nao encontrado"; return; fi
      local _ idx cid reps r
      while read -r _ idx cid reps; do
        IFS=',' read -ra rr <<< "$reps"
        for r in "${rr[@]}"; do
          [ -z "$r" ] && continue
          if is_dead "$r"; then
            PENDING["$r"]="${PENDING[$r]:+${PENDING[$r]},}$cid"
          else
            printf 'op=DELETE\nchunk_id=%s\n' "$cid" | dfs_rpc "$r" >/dev/null
          fi
        done
      done < <(grep '^CHUNK ' "$mf")
      rm -f "$mf"
      clog "arquivo $path removido"
      echo "status=OK" ;;

    LIST_FILES)
      echo "status=OK"
      local mf
      for mf in "$META/files/"*; do
        [ -f "$mf" ] || continue
        local fpath nc nodes="" _ idx cid reps r
        dfs_field "$mf" path; fpath="$FIELD"
        dfs_field "$mf" num_chunks; nc="$FIELD"
        while read -r _ idx cid reps; do
          IFS=',' read -ra rr <<< "$reps"
          for r in "${rr[@]}"; do [[ ",$nodes," == *",$r,"* ]] || nodes="${nodes:+$nodes,}$r"; done
        done < <(grep '^CHUNK ' "$mf")
        echo "FILE $fpath $nc $nodes"
      done ;;

    STATUS)
      echo "status=OK"
      local n cnt=0 mf
      for n in "${MEMBERSHIP[@]}"; do state_of "$n"; echo "NODE $n $ST"; done
      for mf in "$META/files/"*; do [ -f "$mf" ] && cnt=$((cnt + 1)); done
      echo "files=$cnt" ;;

    *)
      echo "status=ERR"; echo "error=op desconhecida: $op" ;;
  esac
}

# ---- Supervisor de re-replicação (rodado no gancho de idle) -----------------
watcher_pass() {
  local n any=0
  for n in "${MEMBERSHIP[@]}"; do
    state_of "$n"; local cur="$ST"
    if [ "$cur" = "DEAD" ] && [ "${PREV_STATE[$n]}" != "DEAD" ]; then
      any=1; clog "detectada MORTE de $n: iniciando re-replicacao"
    fi
    PREV_STATE["$n"]="$cur"
  done
  [ "$any" -eq 0 ] && return

  local mf
  for mf in "$META/files/"*; do
    [ -f "$mf" ] || continue
    local _ idx cid reps
    while read -r _ idx cid reps; do
      local r live="" livecnt=0 deadcnt=0 source=""
      IFS=',' read -ra rr <<< "$reps"
      for r in "${rr[@]}"; do
        if is_dead "$r"; then deadcnt=$((deadcnt + 1)); else live="${live:+$live,}$r"; livecnt=$((livecnt + 1)); [ -z "$source" ] && source="$r"; fi
      done
      { [ "$deadcnt" -eq 0 ] || [ "$livecnt" -eq 0 ]; } && continue
      local need=$((REPLICATION_FACTOR - livecnt)); [ "$need" -le 0 ] && continue

      local t
      for t in "${MEMBERSHIP[@]}"; do
        [ "$need" -le 0 ] && break
        is_dead "$t" && continue
        [[ ",$reps," == *",$t,"* ]] && continue
        local resp; resp="$(printf 'op=REPLICATE\nchunk_id=%s\ntarget=%s\n' "$cid" "$t" | dfs_rpc "$source")"
        dfs_field_str "$resp" status
        if [ "$FIELD" = "OK" ]; then
          local newcsv="${live:+$live,}$t"
          set_chunk_replicas "$mf" "$cid" "$newcsv"
          live="$newcsv"; reps="$newcsv"
          clog "chunk $cid re-replicado $source -> $t"
          need=$((need - 1))
        fi
      done
    done < <(grep '^CHUNK ' "$mf")
  done
}

dfs_on_idle() {
  dfs_now
  [ $((NOW - LAST_WATCH)) -lt "$WATCHER_INTERVAL" ] && return
  LAST_WATCH="$NOW"
  watcher_pass
}

clog "coordenador no ar (mailbox=coordinator)"
clog "membership canonica: ${MEMBERSHIP[*]} (RF=$REPLICATION_FACTOR, quorum=$WRITE_QUORUM)"
dfs_serve coordinator coord_handle
