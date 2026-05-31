"""
Teste manual rápido da RPC ControlService.ListFiles.

Sobe um cliente gRPC, chama ListFiles no coordenador e imprime o resultado.
Pré-requisito: o coordenador precisa estar rodando (start_coordinator.py).
"""

import grpc

from dfs.config import COORDINATOR_HOST, COORDINATOR_PORT
from dfs.pb import dfs_pb2, dfs_pb2_grpc


def main():
    endereco = f"{COORDINATOR_HOST}:{COORDINATOR_PORT}"

    # O "channel" é a conexão de rede com o coordenador. O "stub" é o objeto que
    # parece ter os métodos do serviço, mas cada chamada vira uma chamada remota.
    canal = grpc.insecure_channel(endereco)
    stub = dfs_pb2_grpc.ControlServiceStub(canal)

    # Chamamos a RPC como se fosse um método local: passamos um ListFilesRequest
    # vazio e recebemos um ListFilesResponse de volta.
    resposta = stub.ListFiles(dfs_pb2.ListFilesRequest())

    if not resposta.files:
        print("Nenhum arquivo indexado ainda. (ListFiles respondeu — tudo OK!)")
        return

    for entrada in resposta.files:
        # entrada.nodes_used é um tipo de lista do protobuf; envolvo em list()
        # só para imprimir como lista Python normal.
        print(
            f"{entrada.logical_path} | "
            f"{entrada.total_size_bytes} bytes | "
            f"{entrada.chunk_count} chunk(s) | "
            f"nós: {list(entrada.nodes_used)}"
        )


if __name__ == "__main__":
    main()
