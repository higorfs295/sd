"""
DESCRIÇÃO GERAL:
Este módulo agrupa a lógica de rede do lado do cliente.
Sua função é expor uma interface simples (send_request) para que a aplicação (CLI)
possa enviar comandos sem precisar saber os detalhes do protocolo TCP ou do Framing.
É um exemplo clássico do padrão "Facade".
"""

# Importa as funcionalidades de socket nativas do Python.
import socket

# Importa as configurações globais de conexão (HOST local e PORT).
from dfs.config import HOST, PORT

# Importa as funções de enquadramento para garantir envio e recebimento íntegro de dados.
from dfs.frame import send_frame, recv_frame

# Importa funções para serializar e desserializar as mensagens usando Protobuf.
from dfs.protocol import make_request, parse_response


def send_request(op: str, path: str = "", data: bytes = b""):
    """
    Abre conexão com o servidor, envia a requisição e retorna a resposta.
    """
    # Passo 1: Transforma os parâmetros (op, path, data) em um pacote de bytes Protobuf.
    raw_request = make_request(op=op, path=path, data=data)

    # Passo 2: Cria um socket. AF_INET significa IPv4, SOCK_STREAM significa TCP.
    # O uso do 'with' (gerenciador de contexto) garante que a conexão socket
    # será fechada automaticamente assim que o bloco terminar, mesmo que ocorra um erro.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        
        # Conecta-se ativamente ao endereço do servidor especificado.
        sock.connect((HOST, PORT))
        
        # Envia a requisição em bytes usando nossa função com cabeçalho de tamanho (framing).
        send_frame(sock, raw_request)
        
        # Aguarda (bloqueia) até receber a resposta completa do servidor.
        raw_response = recv_frame(sock)

    # Passo 3: Desserializa os bytes recebidos de volta para um objeto Python estruturado
    # e o retorna para a camada de interface (CLI).
    return parse_response(raw_response)