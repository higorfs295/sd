"""
DESCRIÇÃO GERAL:
Este módulo traduz objetos Python para bytes Protobuf e vice-versa.
Ele funciona como a camada de protocolo do sistema, permitindo que cliente,
coordenador e nós se comuniquem usando a mesma estrutura de mensagens.
"""

from dfs.pb.dfs_pb2 import FileRequest, FileResponse


def make_request(
    op: str,
    path: str = "",
    data: bytes = b"",
    node_id: str = "",
    shard_id: int = 0,
) -> bytes:
    """
    Cria uma requisição Protobuf e serializa para bytes.
    """
    # Cria a mensagem estruturada com os campos necessários.
    request = FileRequest(
        op=op,
        path=path,
        data=data,
        node_id=node_id,
        shard_id=shard_id,
    )

    # Converte a mensagem em bytes para envio pela rede.
    return request.SerializeToString()


def parse_request(raw: bytes) -> FileRequest:
    """
    Converte bytes recebidos em um objeto FileRequest.
    """
    # Instancia uma mensagem vazia.
    request = FileRequest()

    # Preenche a mensagem com os bytes recebidos.
    request.ParseFromString(raw)

    return request


def make_response(
    ok: bool,
    message: str,
    data: bytes = b"",
    entries: list[str] | None = None,
    node_id: str = "",
    shard_id: int = 0,
) -> bytes:
    """
    Cria uma resposta Protobuf e serializa para bytes.
    """
    # Monta a resposta com os campos principais.
    response = FileResponse(
        ok=ok,
        message=message,
        data=data,
        node_id=node_id,
        shard_id=shard_id,
    )

    # Se houver listagem, adiciona os itens ao campo repeated.
    if entries is not None:
        response.entries.extend(entries)

    return response.SerializeToString()


def parse_response(raw: bytes) -> FileResponse:
    """
    Converte bytes recebidos em um objeto FileResponse.
    """
    # Cria uma resposta vazia.
    response = FileResponse()

    # Recupera os campos a partir dos bytes.
    response.ParseFromString(raw)

    return response