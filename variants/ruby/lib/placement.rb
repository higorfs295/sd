# frozen_string_literal: true

# =============================================================================
# placement.rb — Regra de posicionamento determinístico (round-robin).
#
# Porta fiel de placement.py. Função pura: dada a MEMBERSHIP CANÔNICA (a lista
# oficial dos N nós, na mesma ordem) e o índice do chunk, devolve sempre a mesma
# lista de réplicas. É isso que permite ao coordenador e aos nós concordarem
# sobre onde cada chunk mora sem combinar nada em runtime.
#
#   réplicas do chunk i = [ nodes[(i+0) % N], nodes[(i+1) % N], ... (R vezes) ]
#
# A primeira réplica é o primary. INVARIANTE: passe sempre a membership canônica
# (os N), NUNCA só os nós vivos — senão o % N muda e o placement inteiro desloca.
# =============================================================================

module DFS
  module Placement
    module_function

    # Ordena os node_ids pelo sufixo numérico ("node2" < "node10"), estável.
    def sort_nodes(node_ids)
      node_ids.sort_by do |nid|
        m = nid[/(\d+)$/]
        m ? [0, m.to_i, nid] : [1, 0, nid]
      end
    end

    # Réplicas do chunk de índice `chunk_index`.
    def replicas_for_chunk(chunk_index, node_ids, replication_factor = 3, cluster_size: nil)
      raise ArgumentError, "chunk_index negativo: #{chunk_index}" if chunk_index.negative?

      ordered = sort_nodes(node_ids)
      if cluster_size && ordered.size != cluster_size
        raise ArgumentError,
              "cluster divergente: esperado #{cluster_size}, recebido #{ordered.size}. " \
              'O placement EXIGE a membership canônica, não os nós vivos.'
      end
      return [] if ordered.empty?

      n = ordered.size
      r = [replication_factor, n].min
      (0...r).map { |offset| ordered[(chunk_index + offset) % n] }
    end

    # Primary (primeira réplica) do chunk.
    def primary_for_chunk(chunk_index, node_ids, replication_factor = 3, cluster_size: nil)
      replicas_for_chunk(chunk_index, node_ids, replication_factor, cluster_size: cluster_size).first
    end

    # Ingress de um arquivo: round-robin ENTRE arquivos, para nenhum nó virar
    # gargalo eterno de entrada. Exige um contador monotônico no coordenador.
    def ingress_for_file(file_index, node_ids)
      ordered = sort_nodes(node_ids)
      return nil if ordered.empty?

      ordered[file_index % ordered.size]
    end
  end
end
