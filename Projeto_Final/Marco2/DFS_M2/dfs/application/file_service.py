"""
DESCRIÇÃO GERAL:
Esta camada representa o serviço do coordenador.

Ela recebe a requisição da CLI, calcula o destino correto e encaminha a mensagem
para o nó certo do cluster.

Agora ela também:
- mostra a distribuição chunk por chunk;
- registra resumo da distribuição;
- usa fallback de nós quando o primário falha;
- suporta criação de diretórios lógicos (MKDIR).
"""

from collections import Counter

from dfs.cluster.node_client import NodeClient
from dfs.cluster.node_registry import NodeRegistry
from dfs.cluster.sharding import ShardingManager
from dfs.application.metadata_service import MetadataService
from dfs.pb.protocol import parse_request, make_request, make_response
from dfs.config import CHUNK_SIZE


class FileService:
    """
    Serviço central do coordenador.

    Responsabilidades:
    - receber requisições da CLI
    - dividir arquivos em chunks no PUT
    - distribuir chunks entre storage nodes
    - registrar metadados
    - reconstruir arquivos no GET
    - remover chunks no DELETE
    - listar arquivos e diretórios pelo índice
    - criar diretórios lógicos com MKDIR
    """

    def __init__(
        self,
        registry: NodeRegistry | None = None,
        sharding: ShardingManager | None = None,
        metadata: MetadataService | None = None,
        timeout: float = 5.0,
    ):
        self.registry = registry or NodeRegistry()
        self.sharding = sharding or ShardingManager(self.registry)
        self.metadata = metadata or MetadataService()
        self.timeout = timeout

    def _normalize_path(self, path: str) -> str:
        """
        Normaliza um caminho lógico do DFS.

        Isso reduz variações como:
        - espaços acidentais
        - barras invertidas do Windows
        - barras finais desnecessárias
        """
        return path.strip().replace("\\", "/").strip("/")

    def _send_to_node(self, node, op: str, path: str, data: bytes = b"", shard_id: int = 0):
        """
        Encaminha uma operação para um nó específico.

        Este método não faz fallback.
        Ele envia exatamente para o nó recebido como argumento.
        """
        client = NodeClient(node.host, node.port, timeout=self.timeout)

        raw_request = make_request(
            op=op,
            path=path,
            data=data,
            node_id=node.node_id,
            shard_id=shard_id,
        )
        return client.send_raw(raw_request)

    def _send_with_fallback(self, candidates, op: str, path: str, data: bytes = b"", shard_id: int = 0):
        """
        Tenta enviar a operação para uma lista de nós, em ordem.

        O primeiro nó é o primário.
        Os demais funcionam como fallback.
        """
        errors = []

        for attempt, node in enumerate(candidates, start=1):
            try:
                print(
                    f"[COORDENADOR] tentativa {attempt}/{len(candidates)} | "
                    f"op={op} path={path} node={node.node_id} shard={shard_id}"
                )

                response = self._send_to_node(
                    node=node,
                    op=op,
                    path=path,
                    data=data,
                    shard_id=shard_id,
                )

                if response.ok:
                    if attempt > 1:
                        print(
                            f"[COORDENADOR] fallback ativado | "
                            f"path={path} -> node={node.node_id}"
                        )
                    return response, node, attempt

                errors.append(f"{node.node_id}: {response.message}")

            except Exception as exc:
                errors.append(f"{node.node_id}: {exc}")

        raise RuntimeError("; ".join(errors) if errors else "Falha ao enviar para qualquer nó")

    def _split_into_chunks(self, data: bytes) -> list[bytes]:
        """
        Divide os bytes recebidos em chunks de tamanho fixo.
        """
        chunks = []

        for start in range(0, len(data), CHUNK_SIZE):
            chunks.append(data[start : start + CHUNK_SIZE])

        if not chunks:
            chunks.append(b"")

        return chunks

    def _put(self, request):
        """
        Trata a operação PUT.

        Aqui está o ponto principal da distribuição:
        - cada chunk recebe uma decisão própria de shard;
        - a decisão fica visível em log;
        - o metadata grava um resumo da distribuição;
        - se o nó primário falhar, os demais entram como fallback.
        """
        request_path = self._normalize_path(request.path)

        if not request_path:
            return make_response(
                False,
                "Caminho lógico vazio",
                node_id="coordinator",
                shard_id=-1,
            )

        chunks = self._split_into_chunks(request.data)
        chunk_metadata = []

        try:
            for chunk_id, chunk_data in enumerate(chunks):
                # Nó primário calculado de forma determinística.
                primary_shard_id = self.sharding.shard_for_chunk(request_path, chunk_id)
                primary_node = self.sharding.node_for_chunk(request_path, chunk_id)

                # Ordem completa: primário primeiro, depois os outros como fallback.
                candidates = self.sharding.node_candidates_for_chunk(request_path, chunk_id)

                # Caminho físico do chunk dentro do nó.
                chunk_path = self.sharding.chunk_storage_path(request_path, chunk_id)

                # Log explícito para provar visualmente a distribuição.
                print(
                    f"[PUT] path={request_path} chunk={chunk_id} "
                    f"primary_node={primary_node.node_id} primary_shard={primary_shard_id} "
                    f"size={len(chunk_data)}"
                )

                response, chosen_node, attempts = self._send_with_fallback(
                    candidates=candidates,
                    op="PUT",
                    path=chunk_path,
                    data=chunk_data,
                    shard_id=primary_shard_id,
                )

                if not response.ok:
                    return make_response(
                        False,
                        f"Falha ao salvar chunk {chunk_id}: {response.message}",
                        node_id=chosen_node.node_id,
                        shard_id=primary_shard_id,
                    )

                # O shard do armazenamento efetivo é o shard do nó que realmente recebeu o chunk.
                actual_shard_id = self.registry.index_of(chosen_node.node_id)

                chunk_metadata.append(
                    {
                        "chunk_id": chunk_id,
                        "chunk_path": chunk_path,
                        "size": len(chunk_data),
                        "planned_node_id": primary_node.node_id,
                        "planned_shard_id": primary_shard_id,
                        "node_id": chosen_node.node_id,
                        "shard_id": actual_shard_id,
                        "fallback_used": attempts > 1,
                        "attempts": attempts,
                    }
                )

            # Resumo simples da distribuição real.
            nodes_used = Counter(chunk["node_id"] for chunk in chunk_metadata)
            summary_text = ", ".join(f"{node}:{count}" for node, count in sorted(nodes_used.items()))

            self.metadata.put_file(
                path=request_path,
                size=len(request.data),
                chunks=chunk_metadata,
            )

            return make_response(
                True,
                f"Arquivo salvo com {len(chunks)} chunk(s). Distribuição: {summary_text}",
                node_id="coordinator",
                shard_id=-1,
            )

        except Exception as exc:
            return make_response(
                False,
                f"Erro no PUT distribuído: {exc}",
                node_id="coordinator",
                shard_id=-1,
            )

    def _get(self, request):
        """
        Trata a operação GET.
        """
        request_path = self._normalize_path(request.path)

        if not request_path:
            return make_response(
                False,
                "Caminho lógico vazio",
                node_id="coordinator",
                shard_id=-1,
            )

        metadata = self.metadata.get_file(request_path)
        if metadata is None:
            return make_response(
                False,
                "Arquivo não encontrado no índice de metadados",
                node_id="coordinator",
                shard_id=-1,
            )

        chunks = sorted(metadata["chunks"], key=lambda item: item["chunk_id"])
        file_parts = []

        try:
            for chunk in chunks:
                node = self.registry.get(chunk["node_id"])

                response = self._send_to_node(
                    node=node,
                    op="GET",
                    path=chunk["chunk_path"],
                    shard_id=chunk["shard_id"],
                )

                if not response.ok:
                    return make_response(
                        False,
                        f"Falha ao recuperar chunk {chunk['chunk_id']}: {response.message}",
                        node_id=node.node_id,
                        shard_id=chunk["shard_id"],
                    )

                file_parts.append(response.data)

            full_data = b"".join(file_parts)

            return make_response(
                True,
                "Arquivo reconstruído com sucesso",
                data=full_data,
                node_id="coordinator",
                shard_id=-1,
            )

        except Exception as exc:
            return make_response(
                False,
                f"Erro no GET distribuído: {exc}",
                node_id="coordinator",
                shard_id=-1,
            )

    def _delete(self, request):
        """
        Trata a operação DELETE.
        """
        request_path = self._normalize_path(request.path)

        if not request_path:
            return make_response(
                False,
                "Caminho lógico vazio",
                node_id="coordinator",
                shard_id=-1,
            )

        metadata = self.metadata.get_file(request_path)
        if metadata is None:
            return make_response(
                False,
                "Arquivo não encontrado no índice",
                node_id="coordinator",
                shard_id=-1,
            )

        errors = []

        for chunk in metadata["chunks"]:
            try:
                node = self.registry.get(chunk["node_id"])

                response = self._send_to_node(
                    node=node,
                    op="DELETE",
                    path=chunk["chunk_path"],
                    shard_id=chunk["shard_id"],
                )

                if not response.ok:
                    errors.append(f"chunk {chunk['chunk_id']}: {response.message}")

            except Exception as exc:
                errors.append(f"chunk {chunk['chunk_id']}: {exc}")

        if errors:
            return make_response(
                False,
                "Falha parcial ao remover arquivo: " + "; ".join(errors),
                node_id="coordinator",
                shard_id=-1,
            )

        self.metadata.delete_file(request_path)

        return make_response(
            True,
            "Arquivo removido e metadados atualizados",
            node_id="coordinator",
            shard_id=-1,
        )

    def _mkdir(self, request):
        """
        Trata a criação de diretórios lógicos no DFS.

        A criação é:
        - registrada em metadados;
        - enviada para um nó primário;
        - protegida por fallback, caso o nó primário falhe.
        """
        request_path = self._normalize_path(request.path)

        if not request_path:
            return make_response(
                False,
                "Caminho do diretório vazio",
                node_id="coordinator",
                shard_id=-1,
            )

        if self.metadata.exists_file(request_path):
            return make_response(
                False,
                "Já existe um arquivo com esse caminho",
                node_id="coordinator",
                shard_id=-1,
            )

        if self.metadata.exists_directory(request_path):
            return make_response(
                True,
                "Diretório já existia",
                node_id="coordinator",
                shard_id=-1,
            )

        candidates = self.sharding.node_candidates_for_path(request_path)
        primary_node = candidates[0]
        primary_shard_id = self.sharding.shard_for_path(request_path)

        try:
            response, chosen_node, attempts = self._send_with_fallback(
                candidates=candidates,
                op="MKDIR",
                path=request_path,
                shard_id=primary_shard_id,
            )

            if not response.ok:
                return make_response(
                    False,
                    f"Falha ao criar diretório: {response.message}",
                    node_id=chosen_node.node_id,
                    shard_id=primary_shard_id,
                )

            self.metadata.put_directory(
                request_path,
                node_id=chosen_node.node_id,
                shard_id=self.registry.index_of(chosen_node.node_id),
                fallback_used=attempts > 1,
            )

            message = f"Diretório criado com sucesso em {chosen_node.node_id}"
            if attempts > 1:
                message += " (via fallback)"

            return make_response(
                True,
                message,
                node_id=chosen_node.node_id,
                shard_id=self.registry.index_of(chosen_node.node_id),
            )

        except Exception as exc:
            return make_response(
                False,
                f"Erro no MKDIR distribuído: {exc}",
                node_id="coordinator",
                shard_id=-1,
            )

    def _list(self):
        """
        Lista entradas lógicas do DFS.

        Agora a saída pode incluir tanto arquivos quanto diretórios.
        """
        entries = self.metadata.list_entries()

        return make_response(
            True,
            "Listagem feita a partir do índice de metadados",
            entries=entries,
            node_id="coordinator",
            shard_id=-1,
        )

    def dispatch(self, raw_request: bytes) -> bytes:
        """
        Roteia a requisição para a operação correta.
        """
        request = parse_request(raw_request)
        op = request.op.upper().strip()

        try:
            if op == "PUT":
                return self._put(request)
            if op == "GET":
                return self._get(request)
            if op == "DELETE":
                return self._delete(request)
            if op == "MKDIR":
                return self._mkdir(request)
            if op == "LIST":
                return self._list()

            return make_response(
                False,
                "Operação inválida",
                node_id="coordinator",
                shard_id=-1,
            )

        except Exception as exc:
            return make_response(
                False,
                f"Erro inesperado no coordenador: {exc}",
                node_id="coordinator",
                shard_id=-1,
            )