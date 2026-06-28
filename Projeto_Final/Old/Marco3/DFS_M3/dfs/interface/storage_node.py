# dfs/interface/storage_node.py
"""
Servidor de um nó de armazenamento (gRPC). Hospeda, na MESMA porta:
  - DataService        (UploadFile/DownloadFile)  -> CLI
  - ReplicationService (StoreChunk/Fetch/Delete/List) -> outros nós
  - DataPlaneService   (SetUploadPlan/SetDownloadPlan) -> CLI (handoff do plano)
E roda, em background, o registro + heartbeat junto ao coordenador.

Uso:  python -m dfs.interface.storage_node --node-id node1
"""
from __future__ import annotations

import argparse
import shutil
import threading
import time
from concurrent import futures

import grpc

from dfs.config import HEARTBEAT_INTERVAL
from dfs.cluster.node_registry import NodeRegistry
from dfs.cluster.plan_store import PlanStore, DataPlaneServicer
from dfs.cluster.control_client import ControlClient
from dfs.application.data_service import DataServicer
from dfs.application.replication_service import ReplicationServicer
from dfs.storage.local_storage import LocalStorage
from dfs.pb import dfs_pb2_grpc, dataplane_pb2_grpc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dfs-node")
    parser.add_argument("--node-id", required=True, help="identificador do nó")
    parser.add_argument("--no-heartbeat", action="store_true",
                        help="não registrar nem bater heartbeat (testes isolados)")
    return parser


def iniciar_heartbeat(node, storage):
    """Registra o nó e dispara a thread de heartbeat (a cada HEARTBEAT_INTERVAL)."""
    try:
        free = shutil.disk_usage(node.storage_dir).free
        ControlClient().register(node, free)
    except Exception as exc:  # noqa: BLE001
        print(f"[heartbeat] RegisterNode falhou (coordenador no ar?): {exc}")

    def loop():
        while True:
            time.sleep(HEARTBEAT_INTERVAL)
            try:
                ControlClient().heartbeat(
                    node.node_id, shutil.disk_usage(node.storage_dir).free,
                    0, 0, storage.list_chunk_ids())
            except Exception:  # noqa: BLE001
                pass  # coordenador fora do ar: tenta no próximo ciclo

    threading.Thread(target=loop, daemon=True).start()


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    registry = NodeRegistry()
    node = registry.get(args.node_id)
    storage = LocalStorage(root=node.storage_dir)
    plans = PlanStore()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
    dfs_pb2_grpc.add_DataServiceServicer_to_server(
        DataServicer(storage, node.node_id, plans), server)
    dfs_pb2_grpc.add_ReplicationServiceServicer_to_server(
        ReplicationServicer(storage, node.node_id), server)
    dataplane_pb2_grpc.add_DataPlaneServiceServicer_to_server(
        DataPlaneServicer(plans), server)

    server.add_insecure_port(f"{node.host}:{node.port}")
    server.start()
    if not args.no_heartbeat:
        iniciar_heartbeat(node, storage)
    print(f"Nó {node.node_id} ouvindo via gRPC em {node.host}:{node.port}")
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print(f"\nNó {node.node_id} encerrado pelo usuário.")
        server.stop(0)


if __name__ == "__main__":
    main()
