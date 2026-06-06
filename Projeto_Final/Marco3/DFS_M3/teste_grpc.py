# teste_grpc.py  (raiz Marco3/ — teste manual do PLANO DE DADOS)
"""
Teste manual rápido do data plane contra UM nó já no ar.
Substitui o teste_grpc.py legado (que usava o DFSService/FileRequest antigos).

Pré-requisito: um storage node ouvindo (ex.: python -m dfs.interface.storage_node
--node-id node1 --no-heartbeat), na porta 9101.

Exercita o ReplicationService diretamente (StoreChunk -> FetchChunk -> ListChunks
-> DeleteChunk), sem precisar do coordenador.
"""
from dfs.cluster.replication_client import ReplicationClient

HOST, PORT = "127.0.0.1", 9101

def main():
    cli = ReplicationClient(HOST, PORT)
    data = b"Ola Mundo, o data plane gRPC esta a funcionar!"
    chunk_id = "teste_chunk_0"

    print("StoreChunk...")
    r = cli.store_chunk(chunk_id, 0, "teste_upload", "manual", data)
    print(f"  ok={r.ok} bytes={r.bytes_written}")

    print("FetchChunk...")
    de_volta = cli.fetch_chunk(chunk_id, "manual")
    print(f"  iguais={de_volta == data}")

    print("DeleteChunk...")
    ack = cli.delete_chunk(chunk_id)
    print(f"  ok={ack.ok} msg={ack.message}")
    cli.close()

if __name__ == "__main__":
    main()
