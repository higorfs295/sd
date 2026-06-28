# dfs/application/replication_service.py
"""
ReplicationServicer — comunicação NÓ-a-NÓ (e coordenador p/ deleção).
CRUD de chunks no disco local via LocalStorage. Tudo em STREAM_SIZE.
"""
from __future__ import annotations

import grpc

from dfs.config import STREAM_SIZE
from dfs.pb import dfs_pb2, dfs_pb2_grpc


class ReplicationServicer(dfs_pb2_grpc.ReplicationServiceServicer):
    def __init__(self, storage, node_id: str):
        self.storage = storage
        self.node_id = node_id

    def StoreChunk(self, request_iterator, context):
        """Client-streaming: 1ª msg traz metadados; demais, só bytes."""
        chunk_id = None
        buffer = bytearray()
        for msg in request_iterator:
            if chunk_id is None and msg.chunk_id:
                chunk_id = msg.chunk_id
            if msg.data:
                buffer.extend(msg.data)
        if not chunk_id:
            return dfs_pb2.StoreChunkResponse(ok=False, message="sem chunk_id")
        try:
            n = self.storage.store_chunk(chunk_id, bytes(buffer))
            return dfs_pb2.StoreChunkResponse(ok=True, message="OK", bytes_written=n)
        except Exception as exc:  # noqa: BLE001
            return dfs_pb2.StoreChunkResponse(ok=False, message=f"erro: {exc}")

    def FetchChunk(self, request, context):
        """Server-streaming: lê o chunk local e emite em pedaços de STREAM_SIZE."""
        try:
            data = self.storage.read_chunk(request.chunk_id)
        except FileNotFoundError:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"chunk {request.chunk_id} ausente em {self.node_id}")
            return
        for i in range(0, len(data), STREAM_SIZE):
            yield dfs_pb2.FetchChunkResponse(data=data[i:i + STREAM_SIZE])
        if not data:  # chunk vazio: ainda emite uma msg vazia
            yield dfs_pb2.FetchChunkResponse(data=b"")

    def DeleteChunk(self, request, context):
        ok = self.storage.delete_chunk(request.chunk_id)
        return dfs_pb2.Ack(ok=ok, message="apagado" if ok else "não encontrado")

    def ListChunks(self, request, context):
        return dfs_pb2.ListChunksResponse(chunk_ids=self.storage.list_chunk_ids())
