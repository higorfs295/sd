"""
DESCRIÇÃO GERAL:
Este módulo implementa o cliente interno usado pelo coordenador para conversar
com cada nó do cluster. Ele reutiliza o framing e o protocolo já definidos no projeto.
"""

import socket

from dfs.frame import send_frame, recv_frame
from dfs.protocol import parse_response


class NodeClient:
    """
    Cliente TCP para um nó específico.
    """

    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def send_raw(self, raw_request: bytes):
        """
        Envia uma requisição já serializada e recebe a resposta.
        """
        # Cria conexão com timeout para evitar travamento indefinido.
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            # Envia a mensagem já em bytes.
            send_frame(sock, raw_request)

            # Lê a resposta completa.
            raw_response = recv_frame(sock)

        return parse_response(raw_response)