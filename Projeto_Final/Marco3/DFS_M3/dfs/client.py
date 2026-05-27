"""
DESCRIÇÃO GERAL:
Cliente gRPC para o coordenador do DFS.

A conexão TCP (Canal HTTP/2 do gRPC) é mantida viva enquanto o objeto
DFSClient existir.
"""

import grpc

from dfs.config import COORDINATOR_HOST, COORDINATOR_PORT
from dfs.pb import dfs_pb2, dfs_pb2_grpc


class DFSClient:
    """
    Cliente persistente para o coordenador do DFS.

    Uso:
        with DFSClient() as client:
            client.send("PUT", path="docs/a.txt", data=b"oi")
            client.send("GET", path="docs/a.txt")
    """

    def __init__(
        self,
        host: str = COORDINATOR_HOST,
        port: int = COORDINATOR_PORT,
        timeout: float = 300.0,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        
        self.channel = None
        self.stub = None
        self._connect()

    def _connect(self) -> None:
        """
        Abre o canal gRPC (persistente).
        """
        if self.channel is None:
            address = f"{self.host}:{self.port}"
            self.channel = grpc.insecure_channel(address)
            self.stub = dfs_pb2_grpc.DFSServiceStub(self.channel)

    def send(self, op: str, path: str = "", data: bytes = b"") -> dfs_pb2.FileResponse:
        """
        Envia uma requisição via gRPC.
        """
        if self.channel is None:
            self._connect()

        # Constrói o objeto do pedido gRPC
        request = dfs_pb2.FileRequest(
            op=op,
            path=path,
            data=data,
            node_id="cli", # Indica que o pedido vem do utilizador final
            shard_id=-1
        )

        try:
            # Envia para o coordenador e aguarda a resposta
            response = self.stub.ProcessChunk(request, timeout=self.timeout)
            return response
        except grpc.RpcError as e:
            # Devolve um erro formatado caso o coordenador esteja em baixo
            return dfs_pb2.FileResponse(
                ok=False,
                message=f"Erro de comunicação com o coordenador: {e.details()}",
                node_id="cli",
                shard_id=-1
            )

    def close(self) -> None:
        """
        Fecha o canal gRPC explicitamente.
        """
        if self.channel is not None:
            self.channel.close()
            self.channel = None
            self.stub = None

    def __enter__(self) -> "DFSClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def send_request(op: str, path: str = "", data: bytes = b"", client: DFSClient | None = None):
    """
    Wrapper de compatibilidade para executar envios avulsos.
    """
    if client is not None:
        return client.send(op, path=path, data=data)

    with DFSClient() as temp_client:
        return temp_client.send(op, path=path, data=data)