# dfs/cluster/placement.py
"""
placement.py — Regra de posicionamento (placement) de chunks e gateways do DFS.

Função pura compartilhada entre coordenador e nós: dada a mesma entrada, devolve
sempre a mesma saída. É isso que garante que os dois lados calculem o mesmo
placement sem combinar nada em runtime.

Regra (round-robin determinístico por índice de chunk), com N nós e fator R:
    réplicas do chunk i = [ N[(i+0) % N], N[(i+1) % N], ..., N[(i+R-1) % N] ]
A primeira réplica é o primary (grava primeiro / leitura preferida).

INVARIANTE CRÍTICA: a lista de nós passada aqui deve ser a MEMBERSHIP CANÔNICA
(os 5), sempre na mesma ordem, NUNCA a lista de nós vivos. Se um nó cair e você
passar 4 em vez de 5, o `% N` muda e todo o placement desloca — os chunks já
gravados deixam de ser encontrados. Liveness afeta de qual réplica se LÊ ou se
dispara re-replicação; nunca a fórmula. Para blindar, passe `cluster_size`: se
não bater com len(nodes), a função estoura em vez de calcular errado.
"""

from __future__ import annotations

import re
from typing import Any, Sequence, TypeVar

# Tipo genérico do nó: pode ser um NodeRef (protobuf), um dict, ou qualquer
# objeto com .node_id. O módulo só precisa do identificador.
NodeT = TypeVar("NodeT")

DEFAULT_REPLICATION_FACTOR = 3


class PlacementError(ValueError):
    # Erro de placement. Preferimos falhar alto a devolver resultado errado em
    # silêncio: placement errado só aparece no GET, longe da causa.
    pass


# ==============================================================================
# HELPERS INTERNOS
# ==============================================================================

def _node_id(node: Any) -> str:
    # Extrai o node_id seja qual for o tipo do nó. Se vier algo sem id
    # reconhecível, estoura claro — esse é o tipo de divergência entre os dois
    # lados que queremos pegar cedo.
    if hasattr(node, "node_id"):
        return str(node.node_id)
    if isinstance(node, dict) and "node_id" in node:
        return str(node["node_id"])
    raise TypeError(
        f"Nó sem 'node_id' reconhecível (tipo {type(node).__name__}). "
        "Esperado NodeRef, objeto com .node_id, ou dict com 'node_id'."
    )


def _chave_ordenacao(node: Any) -> tuple[int, int, str]:
    # Ordenação numérica e estável. `sorted(key=node_id)` puro seria
    # lexicográfico, e aí "node10" viria antes de "node2" — mudando a ordem,
    # que é a base do determinismo. Extraímos o número final do id; ids sem
    # número caem num balde separado, depois dos numéricos.
    nid = _node_id(node)
    m = re.search(r"(\d+)$", nid)
    if m:
        return (0, int(m.group(1)), nid)
    return (1, 0, nid)


def _ordenar_nos(nodes: Sequence[NodeT]) -> list[NodeT]:
    ordenados = sorted(nodes, key=_chave_ordenacao)

    # Defensivo: dois nós com o mesmo id quebrariam a regra de réplicas
    # distintas (duas "réplicas" no mesmo disco). Pegamos já.
    ids = [_node_id(n) for n in ordenados]
    if len(ids) != len(set(ids)):
        duplicados = sorted({i for i in ids if ids.count(i) > 1})
        raise PlacementError(f"node_ids duplicados na lista de nós: {duplicados}")

    return ordenados


def _validar_entrada(
    nodes: Sequence[NodeT],
    replication_factor: int,
    cluster_size: int | None,
) -> list[NodeT]:
    # Centraliza a validação: R válido, ordenação, duplicatas e — se informado —
    # o cluster_size. A checagem de cluster_size é a blindagem contra passar a
    # lista de nós vivos no lugar da canônica.
    if replication_factor <= 0:
        raise PlacementError(
            f"replication_factor deve ser >= 1 (recebido: {replication_factor})."
        )

    ordenados = _ordenar_nos(nodes)

    if cluster_size is not None and len(ordenados) != cluster_size:
        raise PlacementError(
            f"Tamanho do cluster divergente: esperado {cluster_size}, "
            f"recebido {len(ordenados)}. Passou a membership canônica ou os "
            "nós vivos? O placement EXIGE a membership canônica."
        )

    return ordenados


# ==============================================================================
# API PÚBLICA
# ==============================================================================

def replicas_for_chunk(
    chunk_index: int,
    nodes: Sequence[NodeT],
    replication_factor: int = DEFAULT_REPLICATION_FACTOR,
    cluster_size: int | None = None,
) -> list[NodeT]:
    # Função central: réplicas de um chunk via round-robin determinístico.
    #   chunk 0 -> [N1, N2, N3] ; chunk 3 -> [N4, N5, N1] (o % N dá a volta)
    # A primeira entrada é o primary. Réplicas sempre distintas (r = min(R, N)).
    if chunk_index < 0:
        raise PlacementError(f"chunk_index não pode ser negativo: {chunk_index}")

    ordenados = _validar_entrada(nodes, replication_factor, cluster_size)
    if not ordenados:
        # Cluster vazio: lista vazia em vez de estourar, pois pode ser um estado
        # transitório legítimo (ex.: bootstrap) que o chamador trata.
        return []

    n = len(ordenados)
    # Nunca mais réplicas que nós; garante posições distintas no módulo.
    r = min(replication_factor, n)
    return [ordenados[(chunk_index + offset) % n] for offset in range(r)]


def primary_replica(
    chunk_index: int,
    nodes: Sequence[NodeT],
    replication_factor: int = DEFAULT_REPLICATION_FACTOR,
    cluster_size: int | None = None,
) -> NodeT | None:
    # Atalho: só o primary (primeira réplica). None se o cluster estiver vazio.
    # Reaproveita toda a validação de replicas_for_chunk.
    replicas = replicas_for_chunk(
        chunk_index, nodes, replication_factor, cluster_size
    )
    return replicas[0] if replicas else None


def ingress_for_file(
    file_index: int,
    nodes: Sequence[NodeT],
    cluster_size: int | None = None,
) -> NodeT | None:
    # Escolhe o ingress de um arquivo por round-robin ENTRE arquivos, pra nenhum
    # nó virar gargalo eterno de ingress.
    #
    # Dois alertas:
    #  1. NÃO é stateless no uso: o round-robin por arquivo exige um file_index
    #     monotônico mantido pelo COORDENADOR. A função é pura; o estado vive lá.
    #  2. DIVERGE do comentário atual do .proto, que diz que o ingress "é a
    #     primeira réplica do chunk 0" (= primary_replica(0), sempre N1). Aqui
    #     usamos round-robin por arquivo. >> Corrigir aquele comentário no .proto,
    #     senão você e o Higor divergem na integração. Efeito colateral aceitável:
    #     o ingress pode não ser réplica de nenhum chunk (só repassa bytes) —
    #     decisão do plano de dados, não daqui.
    if file_index < 0:
        raise PlacementError(f"file_index não pode ser negativo: {file_index}")

    # replication_factor não se aplica ao ingress; passamos 1 só pro validador.
    ordenados = _validar_entrada(nodes, replication_factor=1, cluster_size=cluster_size)
    if not ordenados:
        return None

    return ordenados[file_index % len(ordenados)]


# ==============================================================================
# NOTAS DE USO
# ==============================================================================
#
# 1. Sempre passe `cluster_size` (ex.: 5, ou o valor de
#    RegisterNodeResponse.cluster_node_count) ao chamar do coordenador ou do nó.
#    É a rede de segurança contra o bug do `n` errado (passar nós vivos no lugar
#    da membership canônica). Sem isso, a blindagem fica DESLIGADA.
#
# 2. O `file_index` de ingress_for_file precisa de um contador monotônico no
#    coordenador. Se não quiser introduzir esse estado agora, a alternativa
#    stateless é usar primary_replica(0, nodes) como ingress — mas aí aceite o
#    N1 como gargalo de ingress e mantenha o comentário do .proto como está.
#    As duas opções são defensáveis no relatório; escolha UMA e mantenha
#    consistente nos dois lados.