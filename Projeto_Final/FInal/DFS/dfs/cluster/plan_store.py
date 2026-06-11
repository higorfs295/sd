# dfs/cluster/plan_store.py
"""
PlanStore + DataPlaneServicer — handoff do plano de chunks da CLI para o nó.

A CLI chama SetUploadPlan/SetDownloadPlan ANTES de abrir o stream de dados,
entregando o mapa de ChunkPlacement que recebeu do coordenador. O nó guarda em
memória (indexado por upload_id/download_id) e o DataServicer consome no stream.
Decisão registrada na seção 8 do guia (handoff via dataplane.proto).
"""
from __future__ import annotations

import threading

from dfs.pb import dfs_pb2, dataplane_pb2_grpc


class PlanStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._uploads = {}    # upload_id   -> (total, [ChunkPlacement])
        self._downloads = {}  # download_id -> (total, [ChunkPlacement])

    def set_upload(self, upload_id, total, chunks):
        with self._lock:
            self._uploads[upload_id] = (total, list(chunks))

    def get_upload(self, upload_id):
        with self._lock:
            return self._uploads.get(upload_id)

    def clear_upload(self, upload_id):
        with self._lock:
            self._uploads.pop(upload_id, None)

    def set_download(self, download_id, total, chunks):
        with self._lock:
            self._downloads[download_id] = (total, list(chunks))

    def get_download(self, download_id):
        with self._lock:
            return self._downloads.get(download_id)

    def clear_download(self, download_id):
        with self._lock:
            self._downloads.pop(download_id, None)


class DataPlaneServicer(dataplane_pb2_grpc.DataPlaneServiceServicer):
    def __init__(self, plans: PlanStore):
        self.plans = plans

    def SetUploadPlan(self, request, context):
        if not request.upload_id:
            return dfs_pb2.Ack(ok=False, message="upload_id vazio")
        self.plans.set_upload(request.upload_id, request.total_size_bytes, request.chunks)
        return dfs_pb2.Ack(ok=True, message="plano de upload registrado")

    def SetDownloadPlan(self, request, context):
        if not request.download_id:
            return dfs_pb2.Ack(ok=False, message="download_id vazio")
        self.plans.set_download(request.download_id, request.total_size_bytes, request.chunks)
        return dfs_pb2.Ack(ok=True, message="plano de download registrado")
