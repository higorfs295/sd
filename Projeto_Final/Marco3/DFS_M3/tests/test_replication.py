"""
Teste de fumaça do ReplicationService (plano de dados — papel passivo).

Como rodar:
  Terminal 1:  cd DFS_M3 && python -m dfs.interface.storage_node --node-id node1
  Terminal 2:  cd DFS_M3 && python -m tests.test_replication
"""

import grpc
from dfs.pb import dfs_pb2, dfs_pb2_grpc

ENDERECO_NODE1 = "127.0.0.1:9101"


def gerar_store_request(chunk_id, dados):
    # Convenção do .proto: a primeira mensagem traz metadados + bytes.
    yield dfs_pb2.StoreChunkRequest(
        chunk_id=chunk_id,
        chunk_index=0,
        upload_id="upload_teste",
        origin_node_id="cli-teste",
        data=dados,
    )


def main():
    canal = grpc.insecure_channel(ENDERECO_NODE1)
    stub = dfs_pb2_grpc.ReplicationServiceStub(canal)

    chunk_id = "upload_teste_chunk_0"
    conteudo = b"conteudo de teste do chunk"

    # 1) StoreChunk — grava
    resp = stub.StoreChunk(gerar_store_request(chunk_id, conteudo))
    print(f"StoreChunk: ok={resp.ok} msg='{resp.message}' bytes={resp.bytes_written}")

    # 2) ListChunks — apareceu?
    listados = stub.ListChunks(dfs_pb2.ListChunksRequest()).chunk_ids
    print(f"ListChunks: {listados}")
    assert chunk_id in listados, "chunk não apareceu no ListChunks"

    # 3) FetchChunk — lê de volta (DESCOMENTAR depois de implementar o Fetch)
    buffer = bytearray()
    for pedaco in stub.FetchChunk(
        dfs_pb2.FetchChunkRequest(chunk_id=chunk_id, origin_node_id="cli-teste")
    ):
        buffer.extend(pedaco.data)
    print(f"FetchChunk: {len(buffer)} bytes")
    assert bytes(buffer) == conteudo, "bytes lidos diferem dos gravados"

    # 4) DeleteChunk — remove
    ack = stub.DeleteChunk(dfs_pb2.DeleteChunkRequest(chunk_id=chunk_id))
    print(f"DeleteChunk: ok={ack.ok} msg='{ack.message}'")

    # 5) ListChunks — sumiu?
    listados_pos = stub.ListChunks(dfs_pb2.ListChunksRequest()).chunk_ids
    print(f"ListChunks pós-delete: {listados_pos}")
    assert chunk_id not in listados_pos, "chunk não foi removido"

    print("\nOK: ciclo Store -> List -> Delete passou.")


if __name__ == "__main__":
    main()