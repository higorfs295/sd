# frozen_string_literal: true

# =============================================================================
# config.rb — Parâmetros centralizados do DFS (variante Ruby).
#
# Equivale ao dfs/config.py do projeto original em Python. Concentra portas,
# número de nós, fator de replicação, tamanho de chunk e limiares de heartbeat
# num único lugar, para evitar valores espalhados pelo código.
# =============================================================================

require 'fileutils'

module DFS
  module Config
    # ---- Coordenador (plano de controle) ------------------------------------
    COORDINATOR_HOST = ENV.fetch('COORDINATOR_HOST', '127.0.0.1')
    COORDINATOR_PORT = (ENV['COORDINATOR_PORT'] || 9100).to_i

    # ---- Nós de armazenamento (plano de dados) ------------------------------
    # node1 -> 9101, node2 -> 9102, ...
    NODE_COUNT      = (ENV['NODE_COUNT'] || 5).to_i
    BASE_NODE_PORT  = 9101
    NODE_HOST       = ENV.fetch('NODE_HOST', '127.0.0.1')

    # ---- Replicação e chunking ----------------------------------------------
    REPLICATION_FACTOR = 3          # cópias de cada chunk
    WRITE_QUORUM       = 2          # confirmações mínimas para aceitar a escrita

    MIN_CHUNK_SIZE = 1 * 1024 * 1024   # 1 MB (reduzido p/ facilitar demonstração)
    MAX_CHUNK_SIZE = 16 * 1024 * 1024  # 16 MB
    CHUNK_TARGET_MULTIPLIER = 3        # alvo ≈ 3 × nº de nós (over-partitioning)

    # ---- Detecção de falhas (heartbeat) -------------------------------------
    HEARTBEAT_INTERVAL = 2          # segundos entre batimentos de cada nó
    HEARTBEAT_SUSPECT  = 5          # silêncio (s) para virar SUSPECT
    HEARTBEAT_DEAD     = 12         # silêncio (s) para virar DEAD
    WATCHER_INTERVAL   = 2          # varredura do supervisor de re-replicação

    # ---- Diretórios ---------------------------------------------------------
    BASE_DIR     = File.expand_path(File.join(__dir__, 'data'))
    METADATA_DIR = File.join(BASE_DIR, 'metadata')
    METADATA_FILE = File.join(METADATA_DIR, 'metadata_index.json')
    NODES_DIR    = File.join(BASE_DIR, 'nodes')

    module_function

    # Gera a configuração canônica dos nós (membership do cluster).
    # Espelha build_nodes() do config.py original.
    def build_nodes(count = NODE_COUNT)
      (1..count).map do |i|
        {
          'node_id'     => "node#{i}",
          'host'        => NODE_HOST,
          'port'        => BASE_NODE_PORT + i - 1,
          'storage_dir' => File.join(NODES_DIR, "node#{i}")
        }
      end
    end

    # Ordem canônica dos node_ids (base do placement determinístico).
    def node_order(count = NODE_COUNT)
      build_nodes(count).map { |n| n['node_id'] }
    end
  end
end
