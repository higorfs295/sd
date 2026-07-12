#!/usr/bin/env bash
# =============================================================================
# config.sh — Parâmetros centralizados do DFS (variante shellscript).
#
# Equivale ao dfs/config.py. Concentra número de nós, fator de replicação,
# tamanho de chunk e limiares de heartbeat. Adaptação de transporte: em vez de
# gRPC/Kafka, os processos trocam mensagens pelo SISTEMA DE ARQUIVOS, usando
# diretórios-caixa-postal (spool) — fiel ao modelo "processos locais + arquivos".
# =============================================================================

# ---- Cluster -----------------------------------------------------------------
NODE_COUNT="${NODE_COUNT:-5}"
REPLICATION_FACTOR=3
WRITE_QUORUM=2

# Tamanho de chunk (bytes). Reduzido para facilitar a demonstração.
MIN_CHUNK_SIZE=$((1 * 1024 * 1024))    # 1 MB
MAX_CHUNK_SIZE=$((16 * 1024 * 1024))   # 16 MB
CHUNK_TARGET_MULTIPLIER=3

# ---- Heartbeat / detecção de falhas -----------------------------------------
HEARTBEAT_INTERVAL=2     # s entre batimentos de cada nó
HEARTBEAT_SUSPECT=5      # silêncio (s) para SUSPECT
HEARTBEAT_DEAD=12        # silêncio (s) para DEAD
WATCHER_INTERVAL=2       # varredura do supervisor de re-replicação

# ---- Diretórios --------------------------------------------------------------
# BASE = pasta deste projeto (definida por quem der source, senão aqui).
: "${BASE:=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
RUN="$BASE/run"                 # caixas-postais (transporte via arquivos)
DATA="$BASE/data"               # chunks físicos dos nós
META="$RUN/coordinator/meta"    # metadados do coordenador
SPOOL="$RUN/spool"              # área temporária para blobs de dados

# Lista canônica de nós (membership): node1 node2 ... nodeN
node_list() {
  local i
  for ((i = 1; i <= NODE_COUNT; i++)); do printf 'node%d ' "$i"; done
}

# Diretório de chunks de um nó.
node_chunks_dir() { echo "$DATA/nodes/$1/chunks"; }
