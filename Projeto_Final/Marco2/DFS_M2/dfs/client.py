"""
DESCRIÇÃO GERAL:
Cliente persistente do DFS

Mantém uma única conexão TCP aberta com o coordenador durante toda a vida do objeto, evitando o overhead de abrir e fechar socket a cada requisição
"""

import socket

from dfs.config import COORDINATOR_HOST, COORDINATOR_PORT
from dfs.frame import send_frame, recv_frame
from dfs.pb.protocol import make_request, parse_response


class DFSClient:
    """
    Cliente persistente para o coordenador do DFS

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
        # O socket é criado já no __init__ e mantido vivo até o close
        # Isso elimina a necessidade de um método connect() separado e de checar "if _sock is None" antes de cada send
        self._sock = socket.create_connection((host, port), timeout=timeout)

    def send(self, op: str, path: str = "", data: bytes = b""):
        """
        Envia uma requisição reusando a conexão atual
        """
        # Serializa a mensagem em Protobuf e envia com framing por tamanho
        raw_request = make_request(op=op, path=path, data=data)
        send_frame(self._sock, raw_request)

        # Aguarda a resposta enquadrada e devolve já desserializada
        raw_response = recv_frame(self._sock)
        return parse_response(raw_response)

    def close(self) -> None:
        # Fecha a conexão TCP
        try:
            self._sock.close()
        except Exception:
            # Socket já estava fechado ou em estado inválido
            pass

    # Suporte a 'with DFSClient() as client:'
    def __enter__(self) -> "DFSClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def send_request(op: str, path: str = "", data: bytes = b""):
    """
    Wrapper para uma única operação para vários comandos da CLI
    Usada pela CLI para enviar requisições para comandos diferentes e dentro de uma única conexão TCP persistente
    Evitando overhead de abrir/fechar socket a cada comando
    """
    with DFSClient() as client:
        return client.send(op, path=path, data=data)
