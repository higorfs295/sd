"""
DESCRIÇÃO GERAL:
Este módulo representa o servidor de um nó de armazenamento utilizando gRPC.
Cada nó roda de forma independente, escutando em uma porta própria e usando seu
próprio diretório físico para persistência.
"""

import argparse
import grpc
from concurrent import futures

from dfs.cluster.node_registry import NodeRegistry
from dfs.storage.local_storage import LocalStorage
from dfs.application.node_service import NodeService
from dfs.pb import dfs_pb2, dfs_pb2_grpc


class StorageNodeServicer(dfs_pb2_grpc.DFSServiceServicer):
    """
    Implementa a interface do serviço gRPC para o Nó de Armazenamento.
    """
    def __init__(self, service: NodeService):
        # Recebe a lógica de aplicação (NodeService) que já existia no Marco 2
        self.service = service

    def ProcessChunk(self, request, context):
        """
        Método chamado remotamente pelo Coordenador.
        Recebe um objeto dfs_pb2.FileRequest e deve retornar um dfs_pb2.FileResponse.
        """
        # Repassa o objeto da requisição diretamente para a camada de serviço.
        # ATENÇÃO: O seu 'service.dispatch()' agora deve retornar diretamente o objeto 
        # dfs_pb2.FileResponse, sem precisar transformar em bytes (sem serializar).
        response = self.service.dispatch(request)
        return response


def build_parser() -> argparse.ArgumentParser:
    """
    Monta os argumentos da linha de comando do nó.
    """
    parser = argparse.ArgumentParser(prog="dfs-node")
    parser.add_argument("--node-id", required=True, help="identificador do nó")
    return parser


def main(argv=None) -> None:
    """
    Sobe um nó de armazenamento individual.
    """
    # Lê o identificador do nó.
    args = build_parser().parse_args(argv)

    # Consulta o cadastro central dos nós.
    registry = NodeRegistry()
    node = registry.get(args.node_id)

    # O shard do nó pode ser derivado da ordem do registry.
    shard_id = registry.index_of(node.node_id)

    # Cada nó usa sua própria pasta física.
    storage = LocalStorage(root=node.storage_dir)

    # Cria a lógica local do nó.
    service = NodeService(storage=storage, node_id=node.node_id, shard_id=shard_id)

    # --- INÍCIO DA CONFIGURAÇÃO DO SERVIDOR gRPC ---
    # Cria o servidor com um pool de threads para atender múltiplas requisições simultâneas.
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # Registra a nossa classe servidora no servidor gRPC.
    dfs_pb2_grpc.add_DFSServiceServicer_to_server(StorageNodeServicer(service), server)

    # Liga o nó à sua porta específica de forma insegura (sem criptografia SSL/TLS).
    endereco = f"{node.host}:{node.port}"
    server.add_insecure_port(endereco)

    print(f"🔥 Nó {node.node_id} ouvindo via gRPC em {endereco}")
    
    # Inicia o servidor em background.
    server.start()

    try:
        # Mantém a thread principal viva aguardando conexões.
        server.wait_for_termination()
    except KeyboardInterrupt:
        # Encerramento manual do nó.
        print(f"\nNó {node.node_id} encerrado pelo usuário.")
        server.stop(0)


if __name__ == "__main__":
    main()