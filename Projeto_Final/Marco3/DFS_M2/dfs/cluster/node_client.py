"""
DESCRIÇÃO GERAL:
Este módulo implementa o cliente interno usado pelo coordenador para conversar
com cada nó do cluster. Agora atualizado para utilizar canais gRPC.
"""

import grpc

from dfs.pb import dfs_pb2, dfs_pb2_grpc


class NodeClient:
    """
    Cliente gRPC para um nó específico.
    """

    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        
        self.endereco = f"{self.host}:{self.port}"
        
        # O canal (channel) gRPC gerencia a conexão física por baixo dos panos.
        # Ele pode (e deve) ser mantido aberto na instância da classe.
        self.channel = grpc.insecure_channel(self.endereco)
        
        # O Stub é o objeto que usamos para invocar os métodos remotos.
        self.stub = dfs_pb2_grpc.DFSServiceStub(self.channel)

    def send_request(self, request_pb: dfs_pb2.FileRequest) -> dfs_pb2.FileResponse:
        """
        Envia uma requisição (Objeto Protobuf) e recebe a resposta do nó.
        Substitui o antigo 'send_raw'.
        """
        try:
            # Invoca a função remota no nó. O gRPC serializa, envia o frame HTTP/2, 
            # recebe e desserializa automaticamente.
            response = self.stub.ProcessChunk(request_pb, timeout=self.timeout)
            return response
            
        except grpc.RpcError as e:
            # Se der timeout ou o nó estiver offline (Heartbeat/Fallback úteis para o Marco 3)
            print(f"Erro gRPC ao comunicar com o nó {self.endereco}: {e.details()}")
            return None

    def close(self):
        """
        Fecha o canal de comunicação explicitamente, se necessário.
        """
        self.channel.close()