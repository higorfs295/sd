"""
DESCRIÇÃO GERAL:
Este módulo centraliza o cadastro dos nós do cluster DFS.

Ele funciona como uma camada de descoberta de nós (node discovery),
permitindo que o coordenador e os storage nodes saibam:
- quais nós existem;
- qual host cada nó utiliza;
- qual porta cada nó escuta;
- onde cada nó armazena seus dados localmente.

Além disso, o NodeRegistry também mantém:
- uma ordem determinística dos nós;
- acesso por índice;
- acesso por ID;
- suporte ao mecanismo de sharding.

Toda a lógica de distribuição depende diretamente desta estrutura.
"""

from dataclasses import dataclass
from pathlib import Path

from dfs.config import NODES, NODE_ORDER


@dataclass(frozen=True)
class NodeInfo:
    """
    Estrutura imutável contendo as informações de um nó.

    frozen=True:
    - impede alterações acidentais após criação;
    - torna o comportamento mais previsível;
    - evita bugs difíceis em sistemas distribuídos.
    """

    node_id: str
    host: str
    port: int
    storage_dir: Path


class NodeRegistry:
    """
    Mantém o catálogo completo dos nós do cluster.

    Responsabilidades:
    - registrar nós;
    - manter ordem determinística;
    - fornecer busca por ID;
    - fornecer busca por índice;
    - informar quantidade total de nós.
    """

    def __init__(self, nodes: dict[str, dict] | None = None):
        """
        Inicializa o registro de nós.

        Se nenhum conjunto de nós for passado manualmente,
        utiliza a configuração global definida em config.py.
        """

        raw_nodes = nodes or NODES

        # Converte o dicionário bruto em objetos NodeInfo.
        self._nodes: dict[str, NodeInfo] = {
            node_id: NodeInfo(
                node_id=node_id,
                host=spec["host"],
                port=spec["port"],
                storage_dir=Path(spec["storage_dir"]),
            )
            for node_id, spec in raw_nodes.items()
        }

        # Mantém uma ordem fixa e previsível dos nós.
        # Isso é importante para o cálculo consistente de shards.
        self._ordered_ids = [
            node_id
            for node_id in NODE_ORDER
            if node_id in self._nodes
        ]

        # Fallback de segurança:
        # se NODE_ORDER estiver vazio por algum motivo,
        # usa a ordem natural dos nós disponíveis.
        if not self._ordered_ids:
            self._ordered_ids = list(self._nodes.keys())

    def list_nodes(self) -> list[NodeInfo]:
        """
        Retorna todos os nós conhecidos pelo cluster.
        """
        return [
            self._nodes[node_id]
            for node_id in self._ordered_ids
        ]

    def get(self, node_id: str) -> NodeInfo:
        """
        Busca um nó pelo identificador textual.

        Exemplo:
            registry.get("node1")
        """
        return self._nodes[node_id]

    def get_by_index(self, index: int) -> NodeInfo:
        """
        Retorna um nó pela posição lógica no cluster.

        O uso de módulo (%) permite:
        - rotação circular;
        - fallback simples;
        - round-robin determinístico.
        """

        node_id = self._ordered_ids[index % len(self._ordered_ids)]
        return self._nodes[node_id]

    def index_of(self, node_id: str) -> int:
        """
        Retorna o índice lógico de um nó.
        """
        return self._ordered_ids.index(node_id)

    def size(self) -> int:
        """
        Retorna a quantidade total de nós registrados.
        """
        return len(self._ordered_ids)