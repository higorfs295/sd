"""
DESCRIÇÃO GERAL:
Processo do coordenador do DFS (servidor gRPC).

O coordenador implementa o ControlService (plano de controle): registro de nós,
heartbeat, autorização de upload/download, deleção e listagem. NUNCA toca em
bytes de arquivos de usuário, pois isso é responsabilidade dos nós (DataService).
"""

import grpc
from concurrent import futures

from dfs.config import COORDINATOR_HOST, COORDINATOR_PORT
from dfs.application.metadata_service import MetadataService
from dfs.pb import dfs_pb2, dfs_pb2_grpc

# ====================================================================== #
# CONTROLSERVICE: Plano de controle do coordenador
# ====================================================================== #


class ControlServiceServicer(dfs_pb2_grpc.ControlServiceServicer):
    """
    Implementa o ControlService do dfs.proto.

    Herdar de dfs_pb2_grpc.ControlServiceServicer significa que esta classe
    promete responder a todas as RPCs do serviço. O gRPC chama o método Python
    com o MESMO nome da RPC (ListFiles, RegisterNode, ...).

    Toda RPC unária tem a forma def NomeDaRpc(self, request, context):
      - request → mensagem de entrada já desserializada pelo gRPC;
      - context → contexto da chamada (define status/erro, lê deadline, etc.);
      - retorno → uma instância da mensagem de saída declarada no .proto.
    """

    def __init__(self, metadata: MetadataService | None = None):
        # O MetadataService persiste o índice de arquivos em JSON (data/metadata/).
        # É o único estado de que o ListFiles precisa. Recebê-lo de fora (injeção
        # de dependência) facilita testar com um índice falso.
        self.metadata = metadata or MetadataService()

    # -------------------- RPC IMPLEMENTADA NESTE PASSO ----------------- #

    def ListFiles(self, request, context):
        """
        Devolve a lista de arquivos conhecidos pelo coordenador.

        Contrato:
            rpc ListFiles (ListFilesRequest) returns (ListFilesResponse);
            message FileEntry {
                string logical_path=1; // caminho lógico do arquivo
                int64 total_size_bytes=2; // tamanho total do arquivo em bytes
                int32 chunk_count=3; // número de chunks do arquivo
                repeated string nodes_used=4; // nós que possuem chunks do arquivo
            }
        """
        # 1) Caminhos já indexados (chaves ordenadas alfabeticamente).
        caminhos = self.metadata.list_files()

        # 2) Monta uma FileEntry por arquivo.
        entradas = []
        for caminho in caminhos:
            # Dicionário salvo pelo MetadataService:
            #   {"path","size","chunks":[...],
            #    "distribution":{"chunk_count","nodes_used":[...]}}
            info = self.metadata.get_file(caminho)
            if info is None:
                # Defensivo: pode ter sido removido entre o list e o get.
                continue

            distribuicao = info.get("distribution", {})

            # 3) Cada argumento aqui deve ter o nome EXATO do campo no .proto.
            entradas.append(
                dfs_pb2.FileEntry(
                    logical_path=info["path"],
                    total_size_bytes=info["size"],
                    chunk_count=distribuicao.get(
                        "chunk_count", len(info.get("chunks", []))
                    ),
                    # `nodes_used` é repeated string → passa-se uma lista Python.
                    nodes_used=distribuicao.get("nodes_used", []),
                )
            )

        # 4) Empacota tudo no ListFilesResponse (`files` é repeated).
        return dfs_pb2.ListFilesResponse(files=entradas)

    # -------------------- RPCs AINDA NÃO IMPLEMENTADAS ----------------- #
    # Marcam a chamada como UNIMPLEMENTED no context: o gRPC devolve um erro
    # claro ao cliente, em vez de uma resposta vazia que pareceria sucesso.

    def _nao_implementada(self, context, nome_rpc):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details(f"RPC {nome_rpc} ainda não foi implementada (em migração).")

    def RegisterNode(self, request, context):
        self._nao_implementada(context, "RegisterNode")
        return dfs_pb2.RegisterNodeResponse(ok=False)

    def Heartbeat(self, request, context):
        self._nao_implementada(context, "Heartbeat")
        return dfs_pb2.HeartbeatResponse(ok=False)

    def RequestUpload(self, request, context):
        self._nao_implementada(context, "RequestUpload")
        return dfs_pb2.RequestUploadResponse(ok=False)

    def ConfirmUpload(self, request, context):
        self._nao_implementada(context, "ConfirmUpload")
        return dfs_pb2.Ack(ok=False)

    def RequestDownload(self, request, context):
        self._nao_implementada(context, "RequestDownload")
        return dfs_pb2.RequestDownloadResponse(ok=False)

    def DeleteFile(self, request, context):
        self._nao_implementada(context, "DeleteFile")
        return dfs_pb2.Ack(ok=False)


# ====================================================================== #
# INICIALIZAÇÃO DO PROCESSO
# ====================================================================== #


def main():
    """Sobe o coordenador via gRPC expondo o ControlService."""
    # Pool de 50 threads: o gRPC despacha cada chamada numa thread daqui,
    # atendendo vários clientes em paralelo.
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=50))

    # Registra o ControlService no servidor.
    dfs_pb2_grpc.add_ControlServiceServicer_to_server(ControlServiceServicer(), server)

    address = f"{COORDINATOR_HOST}:{COORDINATOR_PORT}"
    server.add_insecure_port(address)

    print(f"🚀 Coordenador DFS ouvindo via gRPC em {address}")
    print("   Serviço registrado: ControlService")

    server.start()
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("\nCoordenador encerrado pelo usuário.")
        server.stop(0)


if __name__ == "__main__":
    main()
