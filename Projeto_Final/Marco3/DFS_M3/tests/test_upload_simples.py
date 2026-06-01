"""
Teste do incremento 1 do ingress: manda um stream pequeno para node1 (ingress)
e confirma que UM chunk foi gravado.

Rodar:
  Terminal 1:  cd DFS_M3 && python -m dfs.interface.storage_node --node-id node1
  Terminal 2:  cd DFS_M3 && python -m tests.test_upload_simples
"""

import grpc
from dfs.pb import dfs_pb2, dfs_pb2_grpc

ENDERECO_NODE1 = "127.0.0.1:9101"


def gerar_upload_stream(upload_id, dados, pedaco=8):
    # Simula a CLI quebrando o arquivo em pedaços de transporte pequenos.
    # A primeira mensagem leva o upload_id; as demais, só bytes.
    primeira = True
    for inicio in range(0, len(dados), pedaco):
        fatia = dados[inicio:inicio + pedaco]
        if primeira:
            yield dfs_pb2.UploadChunk(upload_id=upload_id, data=fatia)
            primeira = False
        else:
            yield dfs_pb2.UploadChunk(data=fatia)


def main():
    canal = grpc.insecure_channel(ENDERECO_NODE1)
    stub = dfs_pb2_grpc.DataServiceStub(canal)

    upload_id = "upload_teste_inc1"
    conteudo = b"conteudo de upload do incremento 1 - varios bytes aqui"

    resultado = stub.UploadFile(gerar_upload_stream(upload_id, conteudo))
    print(f"UploadResult: ok={resultado.ok} msg='{resultado.message}' "
          f"chunks={resultado.chunks_written} bytes={resultado.total_bytes_written}")

    # Confere via ReplicationService que o chunk foi parar no disco.
    rep_stub = dfs_pb2_grpc.ReplicationServiceStub(canal)
    listados = rep_stub.ListChunks(dfs_pb2.ListChunksRequest()).chunk_ids
    print(f"ListChunks em node1: {listados}")

    chunk_esperado = f"{upload_id}_chunk_0"
    assert chunk_esperado in listados, "o chunk do upload não apareceu no disco"
    assert resultado.total_bytes_written == len(conteudo), "tamanho gravado diverge"

    print("\nOK: incremento 1 do ingress funcionou.")


if __name__ == "__main__":
    main()