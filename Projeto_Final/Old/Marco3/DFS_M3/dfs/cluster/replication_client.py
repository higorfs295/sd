# dfs/cluster/replication_client.py
"""
DESCRIÇÃO GERAL:
Reúne os dois clientes do ReplicationService, que vivem na mesma camada de
infraestrutura de cluster (ao lado de node_registry e placement):

1. Funções de nível de coordenador (delete_node_chunks / delete_one_chunk):
   usadas pelo COORDENADOR no DeleteFile. O coordenador age como CLIENTE de um
   nó e comanda a deleção física dos chunks (DeleteChunk). Ele não toca em
   bytes — só dá a ordem; quem apaga o arquivo físico é o nó.

2. Classe ReplicationClient:
   usada pelos NÓS na comunicação nó-a-nó. O ingress chama store_chunk() nas
   demais réplicas (fan-out do PUT) e o egress chama fetch_chunk() num peer
   para buscar um chunk que não tem (failover do GET). Todo transporte é feito
   em pedaços de STREAM_SIZE (64 KB), mantendo cada mensagem bem abaixo do
   limite default do gRPC — por isso não é preciso GRPC_OPTIONS.
"""

from __future__ import annotations

import grpc

from dfs.config import STREAM_SIZE
from dfs.pb import dfs_pb2, dfs_pb2_grpc


# =============================================================================
# CLIENTE DO COORDENADOR — deleção comandada (usado no DeleteFile)
# =============================================================================

def delete_node_chunks(
    host: str,
    port: int,
    chunk_ids: list[str],
    timeout: float = 5.0,
) -> tuple[int, int]:
    """
    Manda UM nó apagar VÁRIOS chunks, reusando um único canal para todos os
    chunks daquele nó. O DeleteFile chama esta função para cada nó, passando a
    lista de chunk_ids que aquele nó guarda.

    Se a deleção de um chunk falha (ou o nó está morto), contamos como falha e
    seguimos para o próximo, sem estourar exceção. O DeleteFile não pode travar
    porque um nó não respondeu. O chunk órfão que sobrar é limpo quando o nó
    voltar (Marco 4, via chunks_to_delete no heartbeat).

    Retorna uma tupla (sucessos, falhas) para o chamador agregar no diagnóstico.
    """
    endereco = f"{host}:{port}"
    sucessos = 0
    falhas = 0

    try:
        # 'with' garante que o canal fecha ao fim do bloco, mesmo se algo estourar.
        # Abrimos UMA vez e usamos para todos os chunk_ids deste nó.
        with grpc.insecure_channel(endereco) as canal:
            stub = dfs_pb2_grpc.ReplicationServiceStub(canal)

            for chunk_id in chunk_ids:
                try:
                    resposta = stub.DeleteChunk(
                        dfs_pb2.DeleteChunkRequest(chunk_id=chunk_id),
                        timeout=timeout,
                    )
                    if resposta.ok:
                        sucessos += 1
                    else:
                        # O nó respondeu, mas disse que não apagou (ex.: já não
                        # tinha o chunk). Não é erro de rede, então contamos
                        # como falha branda para o diagnóstico.
                        falhas += 1
                except grpc.RpcError as erro:
                    # Falha por chunk (timeout, etc.): registra e continua.
                    print(
                        f"[DeleteChunk] falha em {chunk_id} @ {endereco}: "
                        f"{erro.details()}"
                    )
                    falhas += 1

    except grpc.RpcError as erro:
        # Falha ao sequer ABRIR o canal / nó morto: todos os chunks deste nó
        # contam como falha. Não derrubamos o DeleteFile.
        print(f"[DeleteChunk] nó {endereco} indisponível: {erro.details()}")
        falhas += len(chunk_ids)

    return sucessos, falhas


def delete_one_chunk(host: str, port: int, chunk_id: str, timeout: float = 5.0) -> bool:
    """
    Atalho para apagar UM único chunk num nó. Reaproveita delete_node_chunks.

    Será usado no Marco 4, quando o nó voltar e o coordenador mandar apagar os
    chunks órfãos que ficaram para trás. O DeleteFile usa a versão em lote
    (delete_node_chunks), que reusa canal.
    """
    sucessos, _ = delete_node_chunks(host, port, [chunk_id], timeout=timeout)
    return sucessos == 1


# =============================================================================
# CLIENTE DOS NÓS — fan-out (PUT) e failover (GET)
# =============================================================================

class ReplicationClient:
    """
    Cliente gRPC do ReplicationService de OUTRO nó.
      - O ingress chama store_chunk() nas demais réplicas (fan-out do PUT).
      - O egress chama fetch_chunk() num peer para buscar um chunk que não tem.
    Tudo em pedaços de STREAM_SIZE (64 KB) — nenhuma mensagem grande, sem GRPC_OPTIONS.
    """

    def __init__(self, host: str, port: int, timeout: float = 30.0):
        self.endereco = f"{host}:{port}"
        self.channel = grpc.insecure_channel(self.endereco)
        self.stub = dfs_pb2_grpc.ReplicationServiceStub(self.channel)
        self.timeout = timeout

    def store_chunk(
        self, chunk_id, chunk_index, upload_id, origin_node_id, data
    ) -> "dfs_pb2.StoreChunkResponse":
        def gen():
            first = True
            for i in range(0, len(data), STREAM_SIZE):
                piece = data[i:i + STREAM_SIZE]
                if first:
                    yield dfs_pb2.StoreChunkRequest(
                        chunk_id=chunk_id, chunk_index=chunk_index,
                        upload_id=upload_id, origin_node_id=origin_node_id, data=piece)
                    first = False
                else:
                    yield dfs_pb2.StoreChunkRequest(data=piece)
            if first:  # chunk vazio: manda só os metadados
                yield dfs_pb2.StoreChunkRequest(
                    chunk_id=chunk_id, chunk_index=chunk_index,
                    upload_id=upload_id, origin_node_id=origin_node_id, data=b"")
        return self.stub.StoreChunk(gen(), timeout=self.timeout)

    def fetch_chunk(self, chunk_id, origin_node_id) -> bytes:
        req = dfs_pb2.FetchChunkRequest(chunk_id=chunk_id, origin_node_id=origin_node_id)
        buf = bytearray()
        for resp in self.stub.FetchChunk(req, timeout=self.timeout):
            buf.extend(resp.data)
        return bytes(buf)

    def delete_chunk(self, chunk_id) -> "dfs_pb2.Ack":
        return self.stub.DeleteChunk(
            dfs_pb2.DeleteChunkRequest(chunk_id=chunk_id),
            timeout=self.timeout,
        )

    def close(self):
        self.channel.close()