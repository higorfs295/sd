"""
DESCRIÇÃO GERAL:
Cliente persistente do DFS.

A diferença principal agora é que a conexão TCP é mantida viva enquanto o objeto
DFSClient existir. Isso evita abrir e fechar socket a cada requisição dentro
do mesmo processo.
"""

import socket

from dfs.config import COORDINATOR_HOST, COORDINATOR_PORT
from dfs.frame import send_frame, recv_frame
from dfs.pb.protocol import make_request, parse_response


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
        timeout: float = 10.0,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._connect()

    def _connect(self) -> None:
        """
        Abre a conexão TCP apenas uma vez.
        """
        if self._sock is None:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)

    def send(self, op: str, path: str = "", data: bytes = b""):
        """
        Envia uma requisição usando a conexão persistente atual.
        """
        if self._sock is None:
            self._connect()

        assert self._sock is not None

        raw_request = make_request(op=op, path=path, data=data)
        send_frame(self._sock, raw_request)
        raw_response = recv_frame(self._sock)
        return parse_response(raw_response)

    def close(self) -> None:
        """
        Fecha a conexão TCP.
        """
        if self._sock is None:
            return

        try:
            self._sock.close()
        finally:
            self._sock = None

    def __enter__(self) -> "DFSClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def send_request(op: str, path: str = "", data: bytes = b"", client: DFSClient | None = None):
    """
    Wrapper de compatibilidade.

    Se um cliente já estiver aberto, ele é reaproveitado.
    Caso contrário, um cliente temporário é criado e fechado ao final.
    """
    if client is not None:
        return client.send(op, path=path, data=data)

    with DFSClient() as temp_client:
        return temp_client.send(op, path=path, data=data)