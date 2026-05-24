"""
DESCRIÇÃO GERAL:
Esta camada representa o serviço do coordenador com suporte a TRIPLA REPLICAÇÃO.

Ela recebe a requisição da CLI, calcula as 3 réplicas corretas para cada 
chunk através do ShardingManager, e replica o dado entre elas usando gRPC.

Garante total compatibilidade com o MetadataService e ShardingManager originais.
No GET, se uma réplica falhar, o failover automático busca o chunk em outro nó vivo.
"""

from collections import Counter

from dfs.cluster.node_client import NodeClient
from dfs.cluster.node_registry import NodeRegistry
from dfs.cluster.sharding import ShardingManager
from dfs.application.metadata_service import MetadataService
from dfs.config import CHUNK_SIZE

# IMPORTAÇÃO gRPC
from dfs.pb import dfs_pb2


class FileService:
    """
    Serviço central do coordenador com Tripla Replicação e tolerância a falhas.
    """

    def __init__(
        self,
        registry: NodeRegistry | None = None,
        sharding: ShardingManager | None = None,
        metadata: MetadataService | None = None,
        timeout: float = 5.0,  # Tempo limite para failover rápido
    ):
        self.registry = registry or NodeRegistry()
        self.sharding = sharding or ShardingManager(self.registry)
        self.metadata = metadata or MetadataService()
        self.timeout = timeout

    def _normalize_path(self, path: str) -> str:
        """
        Normaliza um caminho lógico do DFS.
        """
        return path.strip().replace("\\", "/").strip("/")

    def _get_node_index(self, node_id: str) -> int:
        """
        Descobre o índice (shard_id) de um nó no registro de forma segura,
        usando apenas os métodos size() e get_by_index() validados no sharding.py.
        """
        total = self.registry.size()
        for i in range(total):
            try:
                node = self.registry.get_by_index(i)
                if node and node.node_id == node_id:
                    return i
            except Exception:
                pass
        return 0

    def _find_node_by_id(self, node_id: str):
        """
        Busca o objeto NodeInfo correspondente a um ID de nó.
        """
        total = self.registry.size()
        for i in range(total):
            try:
                node = self.registry.get_by_index(i)
                if node and node.node_id == node_id:
                    return node
            except Exception:
                pass
        return None

    def _send_to_node(self, node, op: str, path: str, data: bytes = b"", shard_id: int = 0) -> dfs_pb2.FileResponse:
        """
        Encaminha uma operação para um nó específico via gRPC.
        """
        client = NodeClient(node.host, node.port, timeout=self.timeout)

        request_pb = dfs_pb2.FileRequest(
            op=op,
            path=path,
            data=data,
            node_id=node.node_id,
            shard_id=shard_id,
        )
        
        response = client.send_request(request_pb)
        
        if response is None:
            return dfs_pb2.FileResponse(
                ok=False,
                message=f"Falha de comunicação gRPC com o nó {node.node_id}",
                node_id=node.node_id,
                shard_id=shard_id
            )
            
        return response

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

    def _put(self, request: dfs_pb2.FileRequest) -> dfs_pb2.FileResponse:
        """
        Trata a operação PUT distribuindo e replicando cada chunk em até 3 nós.
        """
        request_path = self._normalize_path(request.path)

        if not request_path:
            return dfs_pb2.FileResponse(ok=False, message="Caminho lógico vazio", node_id="coordinator", shard_id=-1)

        chunks = self._split_into_chunks(request.data)
        chunk_metadata = []

        try:
            for chunk_id, chunk_data in enumerate(chunks):
                # Usa o método original do seu sharding.py que rotaciona o cluster
                candidates = self.sharding.node_candidates_for_chunk(request_path, chunk_id)
                chunk_path = self.sharding.chunk_storage_path(request_path, chunk_id)
                
                # Seleciona até os 3 primeiros nós da lista circular para tripla replicação
                replicas_alvo = candidates[:3]
                
                print(f"[PUT] path={request_path} chunk={chunk_id} | Gravando em 3 réplicas...")

                saved_replicas = []
                errors = []

                for node in replicas_alvo:
                    node_shard_id = self._get_node_index(node.node_id)
                    
                    response = self._send_to_node(
                        node=node,
                        op="PUT",
                        path=chunk_path,
                        data=chunk_data,
                        shard_id=node_shard_id,
                    )

                    if response.ok:
                        print(f"  ✅ Réplica gravada com sucesso no nó {node.node_id}")
                        saved_replicas.append({
                            "node_id": node.node_id,
                            "shard_id": node_shard_id
                        })
                    else:
                        print(f"  ❌ Falha no nó {node.node_id}: {response.message}")
                        errors.append(f"{node.node_id}: {response.message}")

                # Se nenhuma das 3 réplicas pôde ser salva, o chunk foi perdido (Falha crítica)
                if not saved_replicas:
                    return dfs_pb2.FileResponse(
                        ok=False,
                        message=f"Falha crítica: impossível salvar chunk {chunk_id} em qualquer réplica. Detalhes: {'; '.join(errors)}",
                        node_id="coordinator",
                        shard_id=-1
                    )

                # Monta a estrutura preservando "node_id" e "shard_id" na raiz
                # para que o seu metadata_service.py original (linha 89) não quebre!
                chunk_metadata.append(
                    {
                        "chunk_id": chunk_id,
                        "chunk_path": chunk_path,
                        "size": len(chunk_data),
                        "node_id": saved_replicas[0]["node_id"],   # Mantém compatibilidade com a linha 89
                        "shard_id": saved_replicas[0]["shard_id"], # Mantém compatibilidade
                        "replicas": saved_replicas,                # Nova lista completa de réplicas para o GET
                    }
                )

            # Grava no banco de metadados central usando o método original
            self.metadata.put_file(
                path=request_path,
                size=len(request.data),
                chunks=chunk_metadata,
            )

            return dfs_pb2.FileResponse(
                ok=True,
                message=f"Arquivo salvo com {len(chunks)} chunk(s) e tripla replicação concluída com sucesso!",
                node_id="coordinator",
                shard_id=-1,
            )

        except Exception as exc:
            return dfs_pb2.FileResponse(ok=False, message=f"Erro no PUT: {exc}", node_id="coordinator", shard_id=-1)

    def _get(self, request: dfs_pb2.FileRequest) -> dfs_pb2.FileResponse:
        """
        Trata a operação GET com failover transparente caso nós estejam caídos.
        """
        request_path = self._normalize_path(request.path)

        metadata = self.metadata.get_file(request_path)
        if metadata is None:
            return dfs_pb2.FileResponse(ok=False, message="Arquivo não encontrado no índice", node_id="coordinator", shard_id=-1)

        chunks = sorted(metadata["chunks"], key=lambda item: item["chunk_id"])
        file_parts = []

        try:
            for chunk in chunks:
                chunk_id = chunk["chunk_id"]
                chunk_path = chunk["chunk_path"]
                
                # Recupera as réplicas. Se for um metadado legado do Marco 2, reconstrói na hora
                replicas = chunk.get("replicas", [])
                if not replicas:
                    replicas = [{"node_id": chunk["node_id"], "shard_id": chunk["shard_id"]}]

                chunk_recuperado = False
                errors = []

                # Tenta ler de cada réplica registrada sequencialmente (Failover automático)
                for attempt, replica in enumerate(replicas, start=1):
                    node = self._find_node_by_id(replica["node_id"])
                    if not node:
                        continue
                        
                    print(f"[GET] Solicitando chunk {chunk_id} | Tentativa {attempt}/{len(replicas)} no nó {node.node_id}")

                    response = self._send_to_node(
                        node=node,
                        op="GET",
                        path=chunk_path,
                        shard_id=replica["shard_id"],
                    )

                    if response.ok:
                        file_parts.append(response.data)
                        chunk_recuperado = True
                        if attempt > 1:
                            print(f"[GET] ⚠️ Failover ativado com sucesso! Réplica obtida do nó secundário {node.node_id}.")
                        break
                    else:
                        errors.append(f"{node.node_id}: {response.message}")

                # Se vasculhou todas as réplicas e não obteve o chunk, o arquivo está indisponível
                if not chunk_recuperado:
                    return dfs_pb2.FileResponse(
                        ok=False, 
                        message=f"Falha no GET: Chunk {chunk_id} inacessível em todas as réplicas. Erros: {'; '.join(errors)}", 
                        node_id="coordinator", 
                        shard_id=-1
                    )

            full_data = b"".join(file_parts)
            return dfs_pb2.FileResponse(ok=True, message="Arquivo lido com sucesso", data=full_data, node_id="coordinator", shard_id=-1)

        except Exception as exc:
            return dfs_pb2.FileResponse(ok=False, message=f"Erro no GET distribuído: {exc}", node_id="coordinator", shard_id=-1)

    def _delete(self, request: dfs_pb2.FileRequest) -> dfs_pb2.FileResponse:
        """
        Trata a operação DELETE eliminando todas as réplicas de todos os nós.
        """
        request_path = self._normalize_path(request.path)

        metadata = self.metadata.get_file(request_path)
        if metadata is None:
            return dfs_pb2.FileResponse(ok=False, message="Arquivo não encontrado", node_id="coordinator", shard_id=-1)

        for chunk in metadata["chunks"]:
            chunk_path = chunk["chunk_path"]
            replicas = chunk.get("replicas", [])
            if not replicas:
                replicas = [{"node_id": chunk["node_id"], "shard_id": chunk["shard_id"]}]

            # Apaga o chunk físico de todos os nós que possuem réplicas
            for replica in replicas:
                node = self._find_node_by_id(replica["node_id"])
                if node:
                    self._send_to_node(node=node, op="DELETE", path=chunk_path, shard_id=replica["shard_id"])

        # Remove do índice central
        self.metadata.delete_file(request_path)
        return dfs_pb2.FileResponse(ok=True, message="Arquivo e todas as suas réplicas removidos.", node_id="coordinator", shard_id=-1)

    def _list(self) -> dfs_pb2.FileResponse:
        """
        Lista as entradas unificadas do sistema.
        """
        entries = self.metadata.list_entries()
        return dfs_pb2.FileResponse(
            ok=True, 
            message="Listagem concluída", 
            entries=entries, 
            node_id="coordinator", 
            shard_id=-1
        )

    def dispatch(self, request: dfs_pb2.FileRequest) -> dfs_pb2.FileResponse:
        op = request.op.upper().strip()
        try:
            if op == "PUT":
                return self._put(request)
            if op == "GET":
                return self._get(request)
            if op == "DELETE":
                return self._delete(request)
            if op == "LIST":
                return self._list()

            return dfs_pb2.FileResponse(ok=False, message="Operação inválida", node_id="coordinator", shard_id=-1)

        except Exception as exc:
            return dfs_pb2.FileResponse(ok=False, message=f"Erro inesperado: {exc}", node_id="coordinator", shard_id=-1)