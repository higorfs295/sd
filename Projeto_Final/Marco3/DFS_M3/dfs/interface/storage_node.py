"""
DESCRIÇÃO GERAL:
Servidor de um nó de armazenamento (gRPC). Cada nó roda independente, numa porta
própria e com diretório físico próprio.

No contrato A.3 + B.2 o nó implementa DOIS serviços (não mais o DFSService único):
  - DataService        (interface com a CLI): UploadFile / DownloadFile
  - ReplicationService (interface entre nós): StoreChunk / FetchChunk /
                                              DeleteChunk / ListChunks

As classes servicer abaixo são CASCAS FINAS: cada método só lida com o protocolo
gRPC (consumir/produzir stream) e delega a lógica ao NodeService. Toda a
inteligência (ingress, egress, fan-out) mora no NodeService, não aqui.
"""

import argparse
import grpc
from concurrent import futures

from dfs.cluster.node_registry import NodeRegistry
from dfs.storage.local_storage import LocalStorage
from dfs.application.node_service import NodeService
from dfs.pb import dfs_pb2, dfs_pb2_grpc


class DataServicer(dfs_pb2_grpc.DataServiceServicer):
    """
    Interface com a CLI (modo gateway). Só traduz gRPC <-> NodeService.
    """

    def __init__(self, service: NodeService):
        self.service = service

    def UploadFile(self, request_iterator, context):
        """
        PUT (client-streaming). INCREMENTO 1: grava um chunk local, sem fan-out.
        """
        try:
            chunk_id, bytes_gravados = self.service.handle_upload_simples(request_iterator)
            return dfs_pb2.UploadResult(
                ok=True,
                message=f"upload recebido (incremento 1): {chunk_id}",
                chunks_written=1,
                total_bytes_written=bytes_gravados,
            )
        except Exception as exc:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return dfs_pb2.UploadResult(ok=False, message=str(exc))

    def DownloadFile(self, request, context):
        """
        GET (server-streaming). request é um DownloadStart; emitimos uma
        sequência de DownloadChunk dando yield.
        """
        # TODO: ligar ao NodeService.handle_download, que é um gerador de bytes.
        #   for corpo in self.service.handle_download(request.download_id, nodes, 5):
        #       yield dfs_pb2.DownloadChunk(data=corpo, is_last=False)
        #   # opcional: um último com is_last=True
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("Egress ainda não implementado")
        return
        yield  # marca a função como geradora mesmo no caminho não implementado


class ReplicationServicer(dfs_pb2_grpc.ReplicationServiceServicer):
    """
    Interface entre nós (peer-to-peer). Só traduz gRPC <-> NodeService.
    """

    def __init__(self, service: NodeService):
        self.service = service

    def StoreChunk(self, request_iterator, context):
        """
        MÉTODO DE REFERÊNCIA — implementado de verdade.

        PUT lado-réplica: o ingress (outro nó) abre um stream e manda este chunk.
        Convenção do .proto: a PRIMEIRA mensagem traz os metadados (chunk_id,
        chunk_index, upload_id, origin_node_id) + possivelmente já o primeiro
        pedaço de data; as mensagens SEGUINTES trazem só data (continuação).

        Acumulamos todos os bytes do stream e gravamos o chunk via NodeService.
        Como um chunk cabe em memória por definição (ex.: 4MB), acumular é ok.
        """
        chunk_id = None
        upload_id = None
        origin = None
        buffer = bytearray()

        for msg in request_iterator:
            # A primeira mensagem com chunk_id preenchido fixa os metadados.
            if msg.chunk_id and chunk_id is None:
                chunk_id = msg.chunk_id
                upload_id = msg.upload_id
                origin = msg.origin_node_id
            # Toda mensagem pode carregar bytes (inclusive a primeira).
            if msg.data:
                buffer.extend(msg.data)

        # Validação defensiva: sem chunk_id não há como nomear o arquivo.
        if not chunk_id:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("StoreChunk: stream sem chunk_id na primeira mensagem")
            return dfs_pb2.StoreChunkResponse(ok=False, message="faltou chunk_id")

        try:
            gravados = self.service.store_chunk(chunk_id, bytes(buffer))
            return dfs_pb2.StoreChunkResponse(
                ok=True,
                message=f"chunk {chunk_id} gravado em {self.service.node_id}",
                bytes_written=gravados,
            )
        except Exception as exc:
            # Erro local vira resposta controlada — o ingress decide o que fazer
            # com uma réplica que falhou (replicação parcial).
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return dfs_pb2.StoreChunkResponse(ok=False, message=str(exc))

    def FetchChunk(self, request, context):
            """
            GET lado-peer: o egress pede um chunk_id; devolvemos os bytes em stream.
            """
            try:
                dados = self.service.read_chunk(request.chunk_id)
            except FileNotFoundError:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(
                    f"chunk {request.chunk_id} não existe em {self.service.node_id}"
                )
                return
            except Exception as exc:
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(str(exc))
                return

            # Emite os bytes em pedaços de 64 KB para o stream.
            PEDACO = 64 * 1024
            for inicio in range(0, len(dados), PEDACO):
                yield dfs_pb2.FetchChunkResponse(data=dados[inicio:inicio + PEDACO])

    def DeleteChunk(self, request, context):
        """
        Apaga um chunk local. Chamado pelo coordenador (DELETE / limpeza).
        """
        # TODO (trivial): self.service.delete_chunk(request.chunk_id); tratar erro.
        try:
            self.service.delete_chunk(request.chunk_id)
            return dfs_pb2.Ack(ok=True, message=f"chunk {request.chunk_id} removido")
        except Exception as exc:
            return dfs_pb2.Ack(ok=False, message=str(exc))

    def ListChunks(self, request, context):
        """
        Lista os chunks locais. Validação cruzada com os metadados do coordenador.
        """
        # TODO (trivial): devolver self.service.list_chunk_ids().
        return dfs_pb2.ListChunksResponse(chunk_ids=self.service.list_chunk_ids())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dfs-node")
    parser.add_argument("--node-id", required=True, help="identificador do nó")
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)

    registry = NodeRegistry()
    node = registry.get(args.node_id)
    shard_id = registry.index_of(node.node_id)

    storage = LocalStorage(root=node.storage_dir)
    service = NodeService(storage=storage, node_id=node.node_id, shard_id=shard_id)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # AGORA SÃO DOIS REGISTROS (não mais add_DFSServiceServicer_to_server):
    dfs_pb2_grpc.add_DataServiceServicer_to_server(DataServicer(service), server)
    dfs_pb2_grpc.add_ReplicationServiceServicer_to_server(ReplicationServicer(service), server)

    endereco = f"{node.host}:{node.port}"
    server.add_insecure_port(endereco)
    print(f"Nó {node.node_id} ouvindo via gRPC em {endereco} (Data + Replication)")

    server.start()
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print(f"\nNó {node.node_id} encerrado pelo usuário.")
        server.stop(0)


if __name__ == "__main__":
    main()