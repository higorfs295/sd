"""
DESCRIÇÃO GERAL:
Este módulo implementa a lógica de sharding do DFS.

Sharding significa:
dividir os dados entre múltiplos nós do cluster.

Aqui usamos uma estratégia determinística baseada em hash:
- o mesmo caminho sempre gera o mesmo shard;
- o mesmo chunk sempre gera o mesmo nó primário;
- a distribuição permanece previsível.

Além disso:
- chunks diferentes tendem a ir para nós diferentes;
- existe suporte a fallback;
- o coordenador consegue redistribuir operações automaticamente.
"""

from hashlib import sha256

from dfs.cluster.node_registry import NodeRegistry, NodeInfo


class ShardingManager:
    """
    Responsável pelas decisões de distribuição do cluster.

    Esta classe NÃO salva dados.
    Ela apenas responde:
    - qual shard usar;
    - qual nó usar;
    - quais nós tentar em fallback.
    """

    def __init__(self, registry: NodeRegistry | None = None):
        """
        Inicializa o gerenciador de sharding.

        Se nenhum registry for informado,
        utiliza automaticamente o NodeRegistry padrão.
        """
        self.registry = registry or NodeRegistry()

    def _stable_hash(self, text: str) -> int:
        """
        Gera um hash determinístico estável.

        SHA-256 é usado apenas como fonte de bits.
        Convertendo parte do digest para inteiro,
        conseguimos usar operação modular (%).
        """

        digest = sha256(text.encode("utf-8")).digest()

        return int.from_bytes(
            digest[:8],
            byteorder="big",
            signed=False,
        )

    def _ensure_cluster_available(self) -> int:
        """
        Garante que o cluster possui nós disponíveis.
        """

        total = self.registry.size()

        if total <= 0:
            raise RuntimeError("Nenhum nó disponível no cluster")

        return total

    # ============================================================
    # SHARDING POR CAMINHO
    # ============================================================

    def shard_for_path(self, path: str) -> int:
        """
        Calcula o shard primário de um caminho lógico.

        Exemplo:
            docs/teste.txt -> shard 1
        """

        total = self._ensure_cluster_available()

        return self._stable_hash(path) % total

    def node_for_path(self, path: str) -> NodeInfo:
        """
        Retorna o nó primário responsável por um caminho lógico.
        """

        shard_id = self.shard_for_path(path)

        return self.registry.get_by_index(shard_id)

    def node_candidates_for_path(self, path: str) -> list[NodeInfo]:
        """
        Retorna todos os candidatos possíveis para fallback.

        Ordem:
        - primeiro nó primário;
        - depois os demais em rotação circular.
        """

        total = self._ensure_cluster_available()

        start = self.shard_for_path(path)

        return [
            self.registry.get_by_index(start + offset)
            for offset in range(total)
        ]

    # ============================================================
    # SHARDING POR CHUNK
    # ============================================================

    def shard_for_chunk(self, path: str, chunk_id: int) -> int:
        """
        Calcula o shard de um chunk específico.

        A chave mistura:
        - caminho lógico;
        - número do chunk.

        Isso ajuda a espalhar os chunks pelo cluster.
        """

        total = self._ensure_cluster_available()

        key = f"{path}::{chunk_id}"

        return self._stable_hash(key) % total

    def node_for_chunk(self, path: str, chunk_id: int) -> NodeInfo:
        """
        Retorna o nó primário responsável pelo chunk.
        """

        shard_id = self.shard_for_chunk(path, chunk_id)

        return self.registry.get_by_index(shard_id)

    def node_candidates_for_chunk(
        self,
        path: str,
        chunk_id: int,
    ) -> list[NodeInfo]:
        """
        Retorna a lista de fallback para um chunk.

        Exemplo:
            node2 -> node3 -> node1
        """

        total = self._ensure_cluster_available()

        start = self.shard_for_chunk(path, chunk_id)

        return [
            self.registry.get_by_index(start + offset)
            for offset in range(total)
        ]

    # ============================================================
    # STORAGE PATHS
    # ============================================================

    def chunk_storage_path(self, path: str, chunk_id: int) -> str:
        """
        Gera o caminho físico interno do chunk.

        O usuário nunca vê esse caminho diretamente.
        Ele existe apenas dentro dos storage nodes.
        """

        safe_name = (
            path.replace("/", "_")
            .replace("\\", "_")
            .replace(":", "_")
            .replace(" ", "_")
        )

        return f".chunks/{safe_name}/chunk_{chunk_id:06d}"