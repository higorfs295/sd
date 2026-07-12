#!/usr/bin/env bash
# =============================================================================
# lib.sh — Camada de transporte e utilidades da variante shellscript.
#
# TRANSPORTE (adaptação de gRPC/Kafka): cada servidor (coordenador ou nó) tem
# uma caixa-postal no sistema de arquivos:  run/<servidor>/in  e  run/<servidor>/out.
# Um cliente escreve um arquivo de requisição no 'in' do servidor (de forma
# atômica, via mv) e aguarda o arquivo de resposta no 'out'. O servidor roda um
# laço que consome as requisições, uma a uma, e escreve as respostas. Blobs de
# dados (bytes de chunk) trafegam como arquivos referenciados por caminho, já
# que todos os processos compartilham o mesmo sistema de arquivos.
#
# NOTA DE DESEMPENHO (importante em Cygwin/MSYS): fork() é MUITO caro nestes
# ambientes. Por isso o caminho quente evita subprocessos: usa o builtin
# printf '%(%s)T' no lugar de `date`, um "nap" via FIFO+read no lugar de `sleep`,
# e parsing de mensagens em bash puro no lugar de `sed`. Resultados vão para
# variáveis GLOBAIS (NOW, FIELD) para evitar o fork de `$(...)`.
# =============================================================================

# ---- Relógio sem fork --------------------------------------------------------
NOW=0
dfs_now() { printf -v NOW '%(%s)T' -1; }   # atualiza a global NOW (builtin, sem fork)

# ---- "nap" sem fork (substitui sleep no polling) ----------------------------
# Abre um FIFO em leitura+escrita e faz `read -t` nele: como não há dado, o read
# espera o timeout e volta — tudo builtin, sem criar processo a cada espera.
_NAPFD=""
dfs_nap_setup() {
  [ -n "$_NAPFD" ] && return
  local f="$SPOOL/.nap.$$"
  mkdir -p "$SPOOL"
  mkfifo "$f" 2>/dev/null || true
  exec {_NAPFD}<>"$f"
  rm -f "$f"
}
dfs_nap() { read -t "${1:-0.1}" -u "$_NAPFD" _ 2>/dev/null || true; }

# ---- Caixa-postal e RPC ------------------------------------------------------
dfs_init_mailbox() { mkdir -p "$RUN/$1/in" "$RUN/$1/out"; }

# dfs_rpc <servidor>  — lê o corpo da requisição do stdin, entrega, aguarda e
# imprime a resposta no stdout. (O chamador prepara antes qualquer arquivo de
# dados referenciado por data=<caminho>.)
dfs_rpc() {
  dfs_nap_setup
  local server="$1"
  local reqid; printf -v reqid '%s_%s_%s' "$EPOCHREALTIME" "$$" "$RANDOM"
  reqid="${reqid//./}"
  local tmp="$SPOOL/$reqid.reqtmp"
  # lê o stdin em bash puro (sem `cat`) e grava
  local line
  : > "$tmp"
  while IFS= read -r line; do printf '%s\n' "$line" >> "$tmp"; done
  mv "$tmp" "$RUN/$server/in/$reqid.req"
  local resp="$RUN/$server/out/$reqid.resp" waited=0
  while [ ! -f "$resp" ]; do
    dfs_nap 0.05
    waited=$((waited + 1))
    if [ "$waited" -gt 2400 ]; then printf 'status=ERR\nerror=timeout\n'; return 1; fi
  done
  cat "$resp"
  rm -f "$resp"
}

# dfs_serve <servidor> <handler_fn> — laço principal do servidor. Para cada
# requisição chama:  handler <arquivo_req> <reqid> <servidor>  e captura o
# stdout como resposta. Se existir a função dfs_on_idle, ela roda a cada ciclo.
dfs_serve() {
  dfs_nap_setup
  local server="$1" handler="$2"
  dfs_init_mailbox "$server"
  local indir="$RUN/$server/in" outdir="$RUN/$server/out"
  shopt -s nullglob
  while true; do
    local processed=0 f reqid
    for f in "$indir"/*.req; do
      [ -f "$f" ] || continue
      reqid="${f##*/}"; reqid="${reqid%.req}"
      "$handler" "$f" "$reqid" "$server" > "$outdir/$reqid.resp.tmp"
      mv "$outdir/$reqid.resp.tmp" "$outdir/$reqid.resp"
      rm -f "$f"
      processed=1
    done
    if declare -F dfs_on_idle >/dev/null; then dfs_on_idle; fi
    [ "$processed" -eq 0 ] && dfs_nap 0.05
  done
}

# ---- Parsing de mensagens em bash puro (sem fork) ---------------------------
# dfs_field <arquivo> <chave>  -> resultado na global FIELD
FIELD=""
dfs_field() {
  local k="$2" line pfx="$2="
  FIELD=""
  while IFS= read -r line; do
    if [ "${line#"$pfx"}" != "$line" ]; then FIELD="${line#*=}"; return; fi
  done < "$1"
}
# dfs_field_str <string> <chave> -> global FIELD (para respostas em memória)
dfs_field_str() {
  local k="$2" line pfx="$2="
  FIELD=""
  while IFS= read -r line; do
    if [ "${line#"$pfx"}" != "$line" ]; then FIELD="${line#*=}"; return; fi
  done <<< "$1"
}

# ---- Placement determinístico (round-robin) ---------------------------------
# dfs_replicas <chunk_index> <n> — imprime os node ids das R réplicas.
dfs_replicas() {
  local idx="$1" n="$2" r=$REPLICATION_FACTOR off
  [ "$r" -gt "$n" ] && r=$n
  local nodes=($(node_list))
  local out=""
  for ((off = 0; off < r; off++)); do out+="${nodes[(idx + off) % n]} "; done
  echo "$out"
}

# ---- Chunking adaptável (espelha chunking.py) -------------------------------
dfs_chunk_size() {
  local fs="$1" cs="$2" cand
  if [ "$fs" -le 0 ]; then echo "$MIN_CHUNK_SIZE"; return; fi
  cand=$((fs / (cs * CHUNK_TARGET_MULTIPLIER)))
  if [ "$fs" -ge $((cs * MIN_CHUNK_SIZE)) ]; then
    local alt=$((fs / cs)); [ "$alt" -lt "$cand" ] && cand=$alt
  fi
  [ "$cand" -lt "$MIN_CHUNK_SIZE" ] && cand=$MIN_CHUNK_SIZE
  [ "$cand" -gt "$MAX_CHUNK_SIZE" ] && cand=$MAX_CHUNK_SIZE
  echo "$cand"
}

dfs_log() { printf '[%s] %s\n' "$1" "$2"; }
