"""
DESCRIÇÃO GERAL:
Este módulo representa o coordenador do DFS via gRPC.
Ele recebe as requisições da CLI, processa o roteamento e encaminha as operações
para o nó correto do cluster usando o FileService.
O gRPC já trata de múltiplos clientes em paralelo automaticamente.
"""

import grpc
from concurrent import futures

from dfs.config import COORDINATOR_HOST, COORDINATOR_PORT
from dfs.application.file_service import FileService
from dfs.pb import dfs_pb2_grpc


class CoordinatorServicer(dfs_pb2_grpc.DFSServiceServicer):
    """
    Ponte entre o Servidor gRPC e a lógica do Coordenador.
    """
    def __init__(self, service: FileService):
        self.service = service

    def ProcessChunk(self, request, context):
        """
        Recebe a requisição (FileRequest) do cliente CLI e passa para o FileService.
        Retorna o FileResponse gerado pelo FileService.
        """
        return self.service.dispatch(request)


def main():
    """
    Inicializa o coordenador do DFS usando gRPC.
    """
    # FileService mantém o estado (metadados, etc.)
    service = FileService()

    # Cria o servidor gRPC com um pool de 50 threads (pode atender 50 clientes simultâneos)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=50))
    
    # Acopla a nossa classe servidora ao servidor gRPC
    dfs_pb2_grpc.add_DFSServiceServicer_to_server(CoordinatorServicer(service), server)

    # Liga o socket ao endereço configurado
    address = f"{COORDINATOR_HOST}:{COORDINATOR_PORT}"
    server.add_insecure_port(address)

    print(f"🚀 Coordenador DFS ouvindo via gRPC em {address}")
    
    server.start()

    try:
        # Mantém o processo principal vivo
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("\nCoordenador encerrado pelo usuário.")
        server.stop(0)


if __name__ == "__main__":
    main()