# dfs/cluster/control_client.py
"""
Cliente gRPC do ControlService (coordenador), usado pelo NÓ:
  - register()      ao subir (RegisterNode)
  - heartbeat()     periodicamente, com block report
  - confirm_upload() pelo ingress ao terminar o PUT
E também usado pela CLI para RequestUpload/RequestDownload/DeleteFile/ListFiles.
Endereço default: COORDINATOR_HOST:COORDINATOR_PORT do config.py.
"""

from __future__ import annotations

import grpc

from dfs.config import COORDINATOR_HOST, COORDINATOR_PORT
from dfs.pb import dfs_pb2, dfs_pb2_grpc


class ControlClient:
    def __init__(
        self,
        host: str = COORDINATOR_HOST,
        port: int = COORDINATOR_PORT,
        timeout: float = 30.0,
    ):
        self.channel = grpc.insecure_channel(f"{host}:{port}")
        self.stub = dfs_pb2_grpc.ControlServiceStub(self.channel)
        self.timeout = timeout

    # ----- usado pelo nó -----
    def register(self, node, free_space_bytes):
        ref = dfs_pb2.NodeRef(node_id=node.node_id, host=node.host, port=node.port)
        return self.stub.RegisterNode(
            dfs_pb2.RegisterNodeRequest(node=ref, free_space_bytes=free_space_bytes),
            timeout=self.timeout,
        )

    def heartbeat(
        self, node_id, free_space_bytes, active_uploads, active_downloads, chunk_ids
    ):
        return self.stub.Heartbeat(
            dfs_pb2.HeartbeatRequest(
                node_id=node_id,
                free_space_bytes=free_space_bytes,
                active_uploads=active_uploads,
                active_downloads=active_downloads,
                chunk_ids=list(chunk_ids),
            ),
            timeout=self.timeout,
        )

    def confirm_upload(self, upload_id, chunks, total_size_bytes):
        return self.stub.ConfirmUpload(
            dfs_pb2.ConfirmUploadRequest(
                upload_id=upload_id,
                chunks=list(chunks),
                total_size_bytes=total_size_bytes,
            ),
            timeout=self.timeout,
        )

    # ----- usado pela CLI -----
    def request_upload(self, logical_path, total_size_bytes, client_request_id=""):
        return self.stub.RequestUpload(
            dfs_pb2.RequestUploadRequest(
                logical_path=logical_path,
                total_size_bytes=total_size_bytes,
                client_request_id=client_request_id,
            ),
            timeout=self.timeout,
        )

    def request_download(self, logical_path, client_request_id=""):
        return self.stub.RequestDownload(
            dfs_pb2.RequestDownloadRequest(
                logical_path=logical_path, client_request_id=client_request_id
            ),
            timeout=self.timeout,
        )

    def delete_file(self, logical_path):
        return self.stub.DeleteFile(
            dfs_pb2.DeleteFileRequest(logical_path=logical_path), timeout=self.timeout
        )

    def list_files(self):
        return self.stub.ListFiles(dfs_pb2.ListFilesRequest(), timeout=self.timeout)

    def close(self):
        self.channel.close()

    def update_chunk_replicas(self, chunk_id, added_node_id, removed_node_id):
        # Fecha o ciclo da re-replicação: troca o nó morto pelo novo nas réplicas do chunk.
        return self.stub.UpdateChunkReplicas(
            dfs_pb2.UpdateChunkReplicasRequest(
                chunk_id=chunk_id,
                added_node_id=added_node_id,
                removed_node_id=removed_node_id,
            )
        )
