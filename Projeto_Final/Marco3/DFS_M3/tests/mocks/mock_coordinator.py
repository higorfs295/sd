"""
Mock de coordenador (ControlService) para testar o plano de dados isoladamente.

Substitui o ControlService real (que é trabalho da Vitória) por respostas
hardcoded, permitindo testar o gateway dos nós (ingress/egress) ponta a ponta
sem depender do coordenador de verdade.

Sobe na MESMA porta do coordenador real (9100), então NÃO rode junto com o
coordenador verdadeiro — é um ou outro.

Como rodar (a partir de DFS_M3/):
    python -m tests.mocks.mock_coordinator
"""

import grpc
from concurrent import futures

from dfs.config import COORDINATOR_HOST, COORDINATOR_PORT, NODE_COUNT
from dfs.cluster.node_registry import NodeRegistry
from dfs.pb import dfs_pb2, dfs_pb2_grpc


class MockCoordinator(dfs_pb2_grpc.ControlServiceServicer):
    """
    Coordenador falso: respostas fixas, só o suficiente para o gateway funcionar.
    """

    def __init__(self):
        # Membership canônica (os 5 nós do config). É a MESMA lista que o
        # placement exige — nunca a lista de "nós vivos".
        self.registry = NodeRegistry()

    def _node_ref(self, node_id: str) -> dfs_pb2.NodeRef:
        # Converte um nó do registry em NodeRef do protobuf.
        info = self.registry.get(node_id)
        return dfs_pb2.NodeRef(node_id=info.node_id, host=info.host, port=info.port)

    # ---- RPCs que o teste de gateway realmente usa ----

    def RequestUpload(self, request, context):
        """
        Etapa 1 do PUT. Devolve um upload_id fixo e aponta node1 como ingress.
        """
        print(f"[MOCK] RequestUpload: path='{request.logical_path}' "
              f"size={request.total_size_bytes}")
        return dfs_pb2.RequestUploadResponse(
            ok=True,
            message="upload autorizado (mock)",
            upload_id="upload_mock_001",
            ingress=self._node_ref("node1"),
        )

    def ConfirmUpload(self, request, context):
        """
        Etapa final do PUT. O ingress reporta os chunks gravados; aqui só
        imprimimos para conferência e respondemos Ack.
        """
        print(f"[MOCK] ConfirmUpload: upload_id='{request.upload_id}' "
              f"total={request.total_size_bytes} chunks={len(request.chunks)}")
        for c in request.chunks:
            replicas = [r.node_id for r in c.replicas]
            print(f"         chunk={c.chunk_id} idx={c.chunk_index} "
                  f"size={c.size_bytes} replicas={replicas}")
        return dfs_pb2.Ack(ok=True, message="confirmado (mock)")

    def RequestDownload(self, request, context):
        """
        Etapa 1 do GET. Devolve egress + a LISTA de chunks do arquivo.

        ATENÇÃO: como é mock, a lista de chunks abaixo é HARDCODED. Para o
        download funcionar de verdade, ela precisa bater com o que foi gravado
        no upload anterior (mesmos chunk_id, mesmas réplicas). Ajuste conforme
        o seu teste — por isso este mock é um ponto de edição manual.
        """
        print(f"[MOCK] RequestDownload: path='{request.logical_path}'")

        # Exemplo de um arquivo de um chunk só, gravado em node1/node2/node3.
        # Troque pelos valores reais do seu upload de teste.
        chunk0 = dfs_pb2.ChunkPlacement(
            chunk_id="upload_mock_001_chunk_0",
            chunk_index=0,
            size_bytes=0,  # ajuste se quiser validar tamanho
            replicas=[
                self._node_ref("node1"),
                self._node_ref("node2"),
                self._node_ref("node3"),
            ],
        )

        return dfs_pb2.RequestDownloadResponse(
            ok=True,
            message="download autorizado (mock)",
            download_id="download_mock_001",
            egress=self._node_ref("node1"),
            total_size_bytes=0,  # ajuste se quiser
            chunks=[chunk0],
        )

    # ---- As demais RPCs do ControlService não são usadas neste teste ----
    # RegisterNode, Heartbeat, DeleteFile, ListFiles ficam como UNIMPLEMENTED
    # herdado da classe base. Os nós não as chamam no startup (confirmado).


def main():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    dfs_pb2_grpc.add_ControlServiceServicer_to_server(MockCoordinator(), server)

    endereco = f"{COORDINATOR_HOST}:{COORDINATOR_PORT}"
    server.add_insecure_port(endereco)
    print(f"[MOCK] Coordenador FALSO ouvindo em {endereco} "
          f"(ControlService, {NODE_COUNT} nós na membership)")

    server.start()
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("\n[MOCK] Coordenador falso encerrado.")
        server.stop(0)


if __name__ == "__main__":
    main()