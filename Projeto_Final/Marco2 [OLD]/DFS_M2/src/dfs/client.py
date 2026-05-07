"""
DESCRIÇÃO GERAL:
Este módulo agrupa a lógica de rede do lado do cliente.
Ele oferece uma interface simples para que a CLI envie comandos sem precisar lidar
diretamente com socket, framing ou serialização.
"""

import socket

from dfs.config import HOST, PORT
from dfs.frame import send_frame, recv_frame
from dfs.protocol import make_request, parse_response


def send_request(
    op: str,
    path: str = "",
    data: bytes = b"",
    node_id: str = "",
    shard_id: int = 0,
):
    """
    Abre conexão com o coordenador, envia a requisição e retorna a resposta.
    """
    # Constrói a mensagem Protobuf já com os metadados novos.
    raw_request = make_request(
        op=op,
        path=path,
        data=data,
        node_id=node_id,
        shard_id=shard_id,
    )

    # Cria o socket TCP e garante o fechamento automático ao sair do bloco.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Conecta ao coordenador do DFS.
        sock.connect((HOST, PORT))

        # Envia a requisição usando framing por tamanho.
        send_frame(sock, raw_request)

        # Aguarda a resposta completa.
        raw_response = recv_frame(sock)

    # Converte os bytes de volta para objeto estruturado.
    return parse_response(raw_response)