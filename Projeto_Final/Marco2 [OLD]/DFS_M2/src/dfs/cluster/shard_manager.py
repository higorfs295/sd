"""
DESCRIÇÃO GERAL:
Este módulo decide em qual shard um arquivo deve ser colocado.
No Marco 2, a estratégia mais simples é usar hashing do caminho lógico do arquivo
e mapear o resultado para um nó do cluster.
"""

from hashlib import sha256

from dfs.cluster.node_registry import NodeRegistry, NodeInfo


class ShardManager:
    """
    Faz o mapeamento entre caminho lógico e nó responsável.
    """

    def __init__(self, registry: NodeRegistry | None = None):
        # Reaproveita o cadastro dos nós.
        self.registry = registry or NodeRegistry()

    def shard_id_for_path(self, path: str) -> int:
        """
        Calcula o shard responsável por um caminho.
        """
        # Gera um hash estável do caminho.
        digest = sha256(path.encode("utf-8")).digest()

        # Usa os primeiros bytes do hash como inteiro.
        value = int.from_bytes(digest[:8], byteorder="big", signed=False)

        # Aplica a divisão modular para escolher o shard.
        return value % self.registry.size()

    def node_for_path(self, path: str) -> NodeInfo:
        """
        Retorna o nó responsável pelo caminho informado.
        """
        shard_id = self.shard_id_for_path(path)
        return self.registry.get_by_index(shard_id)

    def shard_id_for_node(self, node_id: str) -> int:
        """
        Retorna o índice de shard associado ao nó.
        """
        return self.registry.index_of(node_id)

    def node_for_shard(self, shard_id: int) -> NodeInfo:
        """
        Retorna o nó correspondente a um shard.
        """
        return self.registry.get_by_index(shard_id)