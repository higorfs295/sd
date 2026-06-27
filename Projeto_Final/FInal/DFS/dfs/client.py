# dfs/client.py
from __future__ import annotations

import grpc
import os
from dfs.config import STREAM_SIZE
from dfs.pb import dfs_pb2, dfs_pb2_grpc, dataplane_pb2, dataplane_pb2_grpc
from dfs.cluster.control_client import ControlClient


class DataClient:
    """Cliente do nó-gateway (ingress no PUT, egress no GET)."""

    def __init__(self, host: str, port: int):
        self.channel = grpc.insecure_channel(f"{host}:{port}")
        self.data = dfs_pb2_grpc.DataServiceStub(self.channel)
        self.plane = dataplane_pb2_grpc.DataPlaneServiceStub(self.channel)

    def set_upload_plan(self, upload_id: str, total: int, chunks):
        ack = self.plane.SetUploadPlan(dataplane_pb2.UploadPlan(
            upload_id=upload_id, total_size_bytes=total, chunks=chunks))
        if not ack.ok:
            raise RuntimeError(f"ingress recusou o plano: {ack.message}")

    def set_download_plan(self, download_id: str, total: int, chunks):
        ack = self.plane.SetDownloadPlan(dataplane_pb2.DownloadPlan(
            download_id=download_id, total_size_bytes=total, chunks=chunks))
        if not ack.ok:
            raise RuntimeError(f"egress recusou o plano: {ack.message}")

    def upload(self, upload_id: str, data: bytes):
        def gen():
            first = True
            for i in range(0, len(data), STREAM_SIZE):
                piece = data[i:i+STREAM_SIZE]
                if first:
                    yield dfs_pb2.UploadChunk(upload_id=upload_id, data=piece)
                    first = False
                else:
                    yield dfs_pb2.UploadChunk(data=piece)
            if first:   # arquivo vazio
                yield dfs_pb2.UploadChunk(upload_id=upload_id, data=b"")
        return self.data.UploadFile(gen())

    def download(self, download_id: str) -> bytes:
        buf = bytearray()
        for chunk in self.data.DownloadFile(dfs_pb2.DownloadStart(download_id=download_id)):
            buf.extend(chunk.data)
        return bytes(buf)

    def close(self):
        """
        Fecha o canal gRPC aberto com o nó (ingress/egress).

        A CLI (dfs/interface/cli.py) chama este método num bloco `finally` após
        cada operação PUT/GET. Sem ele, a CLI estourava
        'DataClient' object has no attribute 'close' DEPOIS de já ter enviado os
        bytes (o erro era só na limpeza do canal, não no upload em si).
        """
        self.channel.close()

    # =================================================================
    # MÉTODOS DE ALTO NÍVEL (resolvem o nó certo via Coordenador sozinhos)
    # =================================================================

    def upload_file(self, local_path: str, remote_path: str):
        """Lê um arquivo local, obtém o plano com o Coordenador, envia o plano e faz streaming."""
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Arquivo local não encontrado: {local_path}")

        with open(local_path, "rb") as f:
            data = f.read()

        # 1. Solicita as credenciais e estrutura de upload ao Coordenador
        # (ControlClient se conecta usando as portas padrão do config.py)
        control = ControlClient()
        try:
            res = control.request_upload(remote_path, len(data))
        finally:
            control.close()

        # 2. Conecta dinamicamente ao nó de INGRESS determinado pelo Coordenador
        ingress_client = DataClient(res.ingress.host, res.ingress.port)
        try:
            # 3. Registra o plano de alocação de chunks neste nó ingress
            ingress_client.set_upload_plan(res.upload_id, len(data), res.chunks)
            # 4. Transmite o stream de dados para o nó ingress correto
            return ingress_client.upload(res.upload_id, data)
        finally:
            ingress_client.close()

    def download_file(self, remote_path: str, local_path: str):
        """Solicita download ao Coordenador, conecta no nó egress e reconstrói o arquivo."""
        # 1. Solicita a localização dos chunks ao Coordenador
        control = ControlClient()
        try:
            res = control.request_download(remote_path)
        finally:
            control.close()

        # 2. Conecta dinamicamente ao nó de EGRESS determinado pelo Coordenador
        egress_client = DataClient(res.egress.host, res.egress.port)
        try:
            # 3. Informa ao nó egress o plano de recuperação dos chunks
            egress_client.set_download_plan(res.download_id, res.total_size_bytes, res.chunks)
            # 4. Baixa os dados via streaming a partir do nó correto
            data = egress_client.download(res.download_id)
        finally:
            egress_client.close()

        # 5. Grava o arquivo reconstruído no disco local
        with open(local_path, "wb") as f:
            f.write(data)