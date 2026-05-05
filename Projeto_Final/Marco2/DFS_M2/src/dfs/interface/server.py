"""
DESCRIÇÃO GERAL:
Este módulo representa o coordenador do DFS.
Ele recebe as requisições da CLI, processa o roteamento e encaminha as operações
para o nó correto do cluster.
"""

import socket

from dfs.config import HOST, PORT
from dfs.frame import recv_frame, send_frame
from dfs.application.file_service import FileService


def main() -> None:
    """
    Inicializa o coordenador TCP do DFS.
    """
    # Cria o serviço central que fará o roteamento entre os nós.
    service = FileService()

    # Socket do coordenador.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        # Permite reutilizar a porta rapidamente durante testes.
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Liga o socket ao endereço do coordenador.
        server.bind((HOST, PORT))

        # Coloca o servidor em modo de escuta.
        server.listen(5)

        print(f"Coordenador DFS ouvindo em {HOST}:{PORT}")

        try:
            while True:
                # Aguarda conexões de clientes.
                conn, addr = server.accept()
                print(f"Conexão recebida de {addr}")

                # Cada cliente usa sua própria conexão.
                with conn:
                    # Lê a requisição completa.
                    raw_request = recv_frame(conn)

                    # Encaminha a operação para a camada de serviço.
                    raw_response = service.dispatch(raw_request)

                    # Envia a resposta de volta ao cliente.
                    send_frame(conn, raw_response)

        except KeyboardInterrupt:
            # Encerramento manual do servidor.
            print("\nCoordenador encerrado pelo usuário.")


if __name__ == "__main__":
    main()