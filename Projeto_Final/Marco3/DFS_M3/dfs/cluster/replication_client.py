"""
DESCRIÇÃO GERAL:
Cliente que o COORDENADOR usa para chamar o ReplicationService de um nó.

O coordenador age como CLIENTE de um nó: no DeleteFile, ele comanda cada réplica a apagar os chunks que guarda (DeleteChunk).
O coordenador não toca em bytes, ele só dá a ordem. Quem apaga o arquivo físico é o nó.

Vive em dfs/cluster/ por ser infraestrutura de comunicação de cluster, ao lado do node_registry e do placement (mesma camada).
"""

import grpc
from dfs.pb import dfs_pb2, dfs_pb2_grpc


def delete_node_chunks(
    host: str,
    port: int,
    chunk_ids: list[str],
    timeout: float = 5.0,
) -> tuple[int, int]:
    """
    Manda UM nó apagar VÁRIOS chunks, reusando um único canal para todos os chunks daquele nó. O DeleteFile chama esta função para cada nó, passando a lista de chunk_ids que aquele nó guarda.

    Se a deleção de um chunk falha (ou o nó está morto), contamos como falha e seguimos para o próximo, sem estourar exceção.
    O DeleteFile não pode travar porque um nó não respondeu. O chunk órfão que sobrar é limpo quando o nó voltar (Marco 4, via chunks_to_delete no heartbeat).

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
                        # O nó respondeu, mas disse que não apagou (ex.: já não tinha o chunk). Não é erro de rede, então contamos como falha branda para o diagnóstico.
                        falhas += 1
                except grpc.RpcError as erro:
                    # Falha por chunk (timeout, etc.): registra e continua.
                    print(
                        f"[DeleteChunk] falha em {chunk_id} @ {endereco}: "
                        f"{erro.details()}"
                    )
                    falhas += 1

    except grpc.RpcError as erro:
        # Falha ao sequer ABRIR o canal / nó morto: todos os chunks deste nó contam como falha.
        # Não derrubamos o DeleteFile.
        print(f"[DeleteChunk] nó {endereco} indisponível: {erro.details()}")
        falhas += len(chunk_ids)

    return sucessos, falhas


def delete_chunk(host: str, port: int, chunk_id: str, timeout: float = 5.0) -> bool:
    """
    Atalho para apagar UM único chunk num nó. Reaproveita delete_node_chunks.

    Será usado no Marco 4, quando o nó voltar e o coordenador mandar apagar os chunks órfãos que ficaram para trás.
    O DeleteFile usa a versão em lote (delete_node_chunks), que reusa canal.
    """
    sucessos, _ = delete_node_chunks(host, port, [chunk_id], timeout=timeout)
    return sucessos == 1
