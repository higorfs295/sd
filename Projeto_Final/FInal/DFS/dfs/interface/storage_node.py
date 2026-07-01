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
from pathlib import Path
from dfs.cluster.node_registry import NodeRegistry, NodeInfo
from dfs.cluster.kafka_publisher import emit_event


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
        import csv
        from pathlib import Path

        # Log de instrumentação: caminho do CSV (uma linha por batimento real).
        raiz_final = Path(__file__).resolve().parent.parent.parent.parent
        log_path = (
            raiz_final / "benchmark" / "csv" / f"heartbeat_real_{self.node.node_id}.csv"
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)

        novo = not log_path.exists()
        if novo:
            with open(log_path, "w", newline="") as f:
                csv.writer(f).writerow(
                    ["node_id", "timestamp_monotonic", "intervalo_real_s"]
                )

        anterior = time.monotonic()

        while True:
            time.sleep(HEARTBEAT_INTERVAL)
            agora = time.monotonic()
            intervalo_real = agora - anterior  # ESTE é o dado que importa
            anterior = agora

            # Grava o intervalo real ANTES de chamar o coordenador, para o log
            # capturar o atraso mesmo se o heartbeat() em si falhar.
            with open(log_path, "a", newline="") as f:
                csv.writer(f).writerow(
                    [self.node.node_id, agora, round(intervalo_real, 4)]
                )

            try:
                # 1. Guarda a resposta do Coordenador numa variável
                livre = shutil.disk_usage(self.node.storage_dir).free
                inventario = self.storage.list_chunk_ids()
                resposta = self.client.heartbeat(
                    self.node.node_id,
                    livre,
                    0,
                    0,
                    inventario,
                )
                # print(f"[{self.node.node_id}] heartbeat OK")  # linha de depuração

                # Telemetria (best-effort): alimenta o telemetry_hub com a saúde do nó.
                emit_event(
                    "heartbeat",
                    {
                        "node_id": self.node.node_id,
                        "free_space_bytes": livre,
                        "chunks": len(inventario),
                        "intervalo_real_s": round(intervalo_real, 4),
                    },
                )

                # ----------------para todos os efeitos, aqui chamamos o gc, dentro do loop do heartbeat, então é correlato ao m5.
                if hasattr(resposta, "chunks_to_delete") and resposta.chunks_to_delete:
                    for chunk_id in resposta.chunks_to_delete:
                        try:
                            # delete_chunk retorna True se apagou, False se não achou.
                            if self.storage.delete_chunk(chunk_id):
                                print(
                                    f"🧹 [{self.node.node_id}] LIXO COLETADO: Chunk {chunk_id} foi apagado do disco."
                                )
                                emit_event(
                                    "gc_delete",
                                    {"node_id": self.node.node_id, "chunk_id": chunk_id},
                                )
                            else:
                                print(
                                    f"⚠️ [{self.node.node_id}] Chunk {chunk_id} já não existe no disco."
                                )
                        except Exception as erro_io:
                            print(
                                f"🚨 [{self.node.node_id}] Erro de I/O ao apagar {chunk_id}: {erro_io}"
                            )
            # -------------

            except Exception as e:
                # Mantém o teu print ou log de erro que já tinhas antes
                print(f"[{self.node.node_id}] Erro no heartbeat: {e}")


class StorageNodeApp:
    """Orquestrador principal do Nó de Armazenamento (Data Plane)."""

    def __init__(
        self, node_id, disable_heartbeat, host=None, port=None, storage_dir=None
    ):
        self.registry = NodeRegistry()
        # Nó do config (node1..node5): identidade fixa.
        # Nó inédito (elasticidade, ex.: node6): monta a identidade pelos argumentos.
        try:
            self.node = self.registry.get(node_id)
        except KeyError:
            if not (host and port and storage_dir):
                raise SystemExit(
                    f"Nó '{node_id}' não está no config.py. Para um nó novo, "
                    "passe --host, --port e --storage-dir."
                )
            self.node = NodeInfo(
                node_id=node_id,
                host=host,
                port=int(port),
                storage_dir=Path(storage_dir),
            )
        self.storage = LocalStorage(root=self.node.storage_dir)
        self.plans = PlanStore()
        self.disable_heartbeat = disable_heartbeat

        # Inicia a configuração do servidor gRPC
        self.server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
        self._configure_grpc_services()

    def _configure_grpc_services(self) -> None:
        dfs_pb2_grpc.add_DataServiceServicer_to_server(
            DataServicer(self.storage, self.node.node_id, self.plans), self.server
        )
        dfs_pb2_grpc.add_ReplicationServiceServicer_to_server(
            ReplicationServicer(self.storage, self.node.node_id), self.server
        )
        dataplane_pb2_grpc.add_DataPlaneServiceServicer_to_server(
            DataPlaneServicer(self.plans), self.server
        )

        self.server.add_insecure_port(f"{self.node.host}:{self.node.port}")

    def run(self) -> None:
        # 1. Liga o Listener do Kafka
        try:
            kafka_listener = DataPlaneCommandListener(
                node_id=self.node.node_id, storage=self.storage
            )
            kafka_listener.start()
        except Exception as e:
            print(f"[{self.node.node_id}] Erro crítico ao iniciar Kafka: {e}")

        # 2. Liga o Servidor gRPC
        self.server.start()

        # Anuncia-se ao coordenador (RegisterNode).
        # Para node1..node5 é idempotente.
        # Para um nó inédito (node6) é isto que o promove à membership canônica via _ensure_canonical_locked. Sem isto, o heartbeat de node6 seria rejeitado.
        if not self.disable_heartbeat:
            try:
                livre = shutil.disk_usage(self.node.storage_dir).free
                ControlClient().register(self.node, livre)
                print(f"[{self.node.node_id}] Registrado no coordenador.")
            except Exception as e:
                print(f"[{self.node.node_id}] Falha ao registrar: {e}")

        # 3. Liga o Heartbeat (se permitido)

        if not self.disable_heartbeat:
            HeartbeatWorker(self.node, self.storage).start()

        print(
            f"[{self.node.node_id}] Data Plane Ativo -> gRPC: {self.node.port} | Kafka: OK"
        )
        self.server.wait_for_termination()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dfs-node")
    parser.add_argument("--node-id", required=True, help="identificador do nó")
    parser.add_argument(
        "--no-heartbeat", action="store_true", help="ignora o coordenador"
    )
    parser.add_argument(
        "--host", default=None, help="host do nó (p/ nó fora do config, ex.: node6)"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="porta gRPC (p/ nó fora do config)"
    )
    parser.add_argument(
        "--storage-dir", default=None, help="pasta de chunks (p/ nó fora do config)"
    )
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    app = StorageNodeApp(
        node_id=args.node_id,
        disable_heartbeat=args.no_heartbeat,
        host=args.host,
        port=args.port,
        storage_dir=args.storage_dir,
    )
    app.run()


if __name__ == "__main__":
    main()
