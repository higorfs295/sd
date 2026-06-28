"""
DESCRIÇÃO GERAL:
Este módulo é a camada de tradução do sistema. Ele faz a interface entre o código Python 
e as classes geradas pelo Protocol Buffers (gRPC/Protobuf).
O Protobuf é usado para serializar dados estruturados de forma altamente eficiente,
permitindo que o Cliente e o Servidor se entendam através de uma "linguagem" (esquema) comum.
"""

# Importamos as classes geradas automaticamente pelo compilador do Protobuf (protoc)
# a partir do arquivo dfs.proto.
from dfs.pb.dfs_pb2 import FileRequest, FileResponse


def make_request(op: str, path: str = "", data: bytes = b"") -> bytes:
    """
    Cria uma requisição Protobuf e serializa para bytes.
    """
    # Instancia o objeto Protobuf de requisição com a operação (GET, PUT, etc.),
    # o caminho do arquivo e os dados em bytes (útil para envio de arquivos no PUT).
    request = FileRequest(op=op, path=path, data=data)
    
    # .SerializeToString() é um método do Protobuf que converte o objeto estruturado
    # em uma sequência de bytes puros para transmissão via rede (socket).
    return request.SerializeToString()


def parse_request(raw: bytes) -> FileRequest:
    """
    Converte bytes recebidos em um objeto FileRequest.
    """
    # Cria um objeto vazio de requisição.
    request = FileRequest()
    
    # Preenche o objeto com os dados da sequência de bytes recebida da rede.
    request.ParseFromString(raw)
    
    # Retorna o objeto estruturado que o Python pode acessar usando notação de ponto (ex: request.op).
    return request


def make_response(
    ok: bool,
    message: str,
    data: bytes = b"",
    entries: list[str] | None = None,
) -> bytes:
    """
    Cria uma resposta Protobuf e serializa para bytes.
    """
    # Instancia o objeto Protobuf de resposta.
    # ok: indica sucesso ou falha da operação; message: feedback humano; data: conteúdo do arquivo (GET).
    response = FileResponse(ok=ok, message=message, data=data)

    # entries é uma lista opcional usada apenas na operação LIST.
    if entries is not None:
        # .extend() é usado no Protobuf para adicionar múltiplos itens a um campo "repeated" (lista/array).
        response.entries.extend(entries)

    # Converte o objeto de resposta para bytes para envio pela rede.
    return response.SerializeToString()


def parse_response(raw: bytes) -> FileResponse:
    """
    Converte bytes recebidos em um objeto FileResponse.
    """
    # Cria objeto de resposta vazio.
    response = FileResponse()
    
    # Desserializa os bytes recebidos da rede preenchendo o objeto de resposta.
    response.ParseFromString(raw)
    
    # Retorna o objeto pronto para uso.
    return response