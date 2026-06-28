# dfs/client.py
from __future__ import annotations

import grpc
from dfs.config import STREAM_SIZE
from dfs.pb import dfs_pb2, dfs_pb2_grpc, dataplane_pb2, dataplane_pb2_grpc
from dfs.cluster.control_client import ControlClient   # já existe em cluster/

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
        self.channel.close()

__all__ = ["ControlClient", "DataClient"]