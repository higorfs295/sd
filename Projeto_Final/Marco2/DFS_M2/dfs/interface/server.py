"""
DESCRIÇÃO GERAL:
Este módulo representa o coordenador do DFS
Ele recebe as requisições da CLI, processa o roteamento e encaminha as operações
para o nó correto do cluster
Aceita múltiplos clientes simultâneos (uma thread por conexão) e suporta conexões persistentes cada cliente envia várias requisições no mesmo socket
"""

import socket
import threading

from dfs.config import COORDINATOR_HOST, COORDINATOR_PORT
from dfs.frame import recv_frame, send_frame
from dfs.application.file_service import FileService


def _client(conn, addr, service):
    """
    Atende um cliente do início ao fim em uma thread dedicada
    Loop interno suporta conexão persistente
    """
    print(f"[Coordenador] conexão aberta com {addr}")

    # 'with conn' garante que o socket é fechado mesmo se houver exceção
    with conn:
        try:
            # Lê e responde requisições enquanto o cliente mantiver o socket aberto
            # Quando ele fechar, recv_frame levanta ConnectionError e saímos do loop.
            while True:
                raw_request = recv_frame(conn)
                raw_response = service.dispatch(raw_request)
                send_frame(conn, raw_response)

        except ConnectionError:
            # Encerramento normal: cliente fechou o socket
            # Não é erro, é o caminho esperado de término da conexão
            pass

        except Exception as exc:
            # Qualquer outra falha é registrada, mas mantém o coordenador de pé
            # A thread atual morre, as outras seguem
            print(f"[Coordenador] erro ao atender {addr}: {exc}")

    print(f"[Coordenador] conexão encerrada com {addr}")


def main():
    """
    Inicializa o coordenador TCP do DFS
    """
    # FileService é compartilhado entre todas as threads
    # O MetadataService interno já tem lock, então isso é seguro
    service = FileService()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        # Permite reutilizar a porta rapidamente entre execuções
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Liga o socket ao endereço configurado
        server.bind((COORDINATOR_HOST, COORDINATOR_PORT))

        # Backlog maior porque agora esperamos vários clientes em paralelo
        server.listen(50)

        print(f"Coordenador DFS ouvindo em {COORDINATOR_HOST}:{COORDINATOR_PORT}")

        try:
            while True:
                # accept() bloqueia até chegar uma nova conexão
                conn, addr = server.accept()

                # Cada conexão vai para uma thread dedicada
                # principal — Ctrl+C encerra tudo limpo.
                thread = threading.Thread(
                    target=_client,
                    args=(conn, addr, service),
                    daemon=True,  # faz a thread morrer junto com o processo
                )
                thread.start()

        # Ctrl+C é capturado para encerrar o servidor de forma limpa
        except KeyboardInterrupt:
            print("\nCoordenador encerrado pelo usuário.")


if __name__ == "__main__":
    main()
