# dfs/interface/storage_node.py
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

from .kafka_listener import DataPlaneCommandListener

class HeartbeatWorker:
    """Isola a responsabilidade de comunicação periódica com o Control Plane."""
    def __init__(self, node: any, storage: LocalStorage):
        self.node = node
        self.storage = storage
        self.client = ControlClient()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        # Garante que tens a biblioteca os importada (podes colocar no topo do ficheiro)
        import os 
        
        while True:
            time.sleep(HEARTBEAT_INTERVAL)
            try:
                # 1. Guarda a resposta do Coordenador numa variável
                resposta = self.client.heartbeat(
                    self.node.node_id, 
                    shutil.disk_usage(self.node.storage_dir).free,
                    0, 0, self.storage.list_chunk_ids() # Atenção: usa o método que já tens aí no teu código original
                )

                # --- 2. INÍCIO DO CÓDIGO DO MARCO 5 (GARBAGE COLLECTION) ---
                if hasattr(resposta, 'chunks_to_delete') and resposta.chunks_to_delete:
                    for chunk_id in resposta.chunks_to_delete:
                        # Descobre o caminho físico do ficheiro no disco
                        caminho_chunk = os.path.join(self.node.storage_dir, chunk_id)
                        
                        try:
                            # Se o ficheiro lá estiver, elimina-o
                            if os.path.exists(caminho_chunk):
                                os.remove(caminho_chunk)
                                print(f"🧹 [{self.node.node_id}] LIXO COLETADO: Chunk {chunk_id} foi apagado do disco.")
                            else:
                                print(f"⚠️ [{self.node.node_id}] Chunk {chunk_id} já não existe no disco.")
                        except Exception as erro_io:
                            print(f"🚨 [{self.node.node_id}] Erro de I/O ao apagar {chunk_id}: {erro_io}")
                # --- FIM DO CÓDIGO ---

            except Exception as e:
                # Mantém o teu print ou log de erro que já tinhas antes
                print(f"[{self.node.node_id}] Erro no heartbeat: {e}")

class StorageNodeApp:
    """Orquestrador principal do Nó de Armazenamento (Data Plane)."""
    def __init__(self, node_id: str, disable_heartbeat: bool):
        self.registry = NodeRegistry()
        self.node = self.registry.get(node_id)
        self.storage = LocalStorage(root=self.node.storage_dir)
        self.plans = PlanStore()
        self.disable_heartbeat = disable_heartbeat
        
        # Inicia a configuração do servidor gRPC
        self.server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
        self._configure_grpc_services()

    def _configure_grpc_services(self) -> None:
        dfs_pb2_grpc.add_DataServiceServicer_to_server(
            DataServicer(self.storage, self.node.node_id, self.plans), self.server)
        dfs_pb2_grpc.add_ReplicationServiceServicer_to_server(
            ReplicationServicer(self.storage, self.node.node_id), self.server)
        dataplane_pb2_grpc.add_DataPlaneServiceServicer_to_server(
            DataPlaneServicer(self.plans), self.server)
        
        self.server.add_insecure_port(f"{self.node.host}:{self.node.port}")

    def run(self) -> None:
        # 1. Liga o Listener do Kafka
        try:
            kafka_listener = DataPlaneCommandListener(node_id=self.node.node_id, storage=self.storage)
            kafka_listener.start()
        except Exception as e:
            print(f"[{self.node.node_id}] Erro crítico ao iniciar Kafka: {e}")

        # 2. Liga o Servidor gRPC
        self.server.start()

        # 3. Liga o Heartbeat (se permitido)
        if not self.disable_heartbeat:
            HeartbeatWorker(self.node, self.storage).start()

        print(f"[{self.node.node_id}] Data Plane Ativo -> gRPC: {self.node.port} | Kafka: OK")
        self.server.wait_for_termination()

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dfs-node")
    parser.add_argument("--node-id", required=True, help="identificador do nó")
    parser.add_argument("--no-heartbeat", action="store_true", help="ignora o coordenador")
    return parser

def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    app = StorageNodeApp(node_id=args.node_id, disable_heartbeat=args.no_heartbeat)
    app.run()

if __name__ == "__main__":
    main()