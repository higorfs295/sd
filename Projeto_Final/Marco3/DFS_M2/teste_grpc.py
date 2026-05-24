from dfs.cluster.node_client import NodeClient
from dfs.pb import dfs_pb2

# ATENÇÃO: Confirme se a porta abaixo (9101) é a mesma que o seu Terminal 1 está mostrando!
cliente = NodeClient(host="127.0.0.1", port=9101) 

# Criar o pedido gRPC (FileRequest)
pedido = dfs_pb2.FileRequest(
    op="PUT",
    path="teste_grpc.txt",
    data=b"Ola Mundo, o gRPC esta a funcionar perfeitamente!",
    node_id="node1",
    shard_id=0
)

print("A enviar o pedido para o nó via gRPC...")
resposta = cliente.send_request(pedido)

if resposta:
    print(f"Resposta do Nó: Sucesso={resposta.ok} | Mensagem='{resposta.message}'")
else:
    print("Falha na comunicação com o nó!")