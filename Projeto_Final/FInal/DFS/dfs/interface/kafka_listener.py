# dfs/interface/kafka_listener.py
from __future__ import annotations

import json
import threading
import os
import time
import grpc
from kafka import KafkaConsumer

from dfs.cluster.node_registry import NodeRegistry
from dfs.storage.local_storage import LocalStorage
from dfs.pb import dfs_pb2, dfs_pb2_grpc
from dfs.cluster.control_client import ControlClient
from dfs.cluster.replication_client import ReplicationClient
from dfs.cluster.net_sim import apply_network_delay
from dfs.cluster.kafka_publisher import emit_event


class DataPlaneCommandListener:
    def __init__(self, node_id: str, storage: LocalStorage, broker_url: str = None):
        self.node_id = node_id
        self.storage = storage
        self.registry = NodeRegistry()
        self.control_client = ControlClient()
        self.topic = f'storage-node-{node_id}-commands'

        self.broker_url = broker_url or os.getenv("KAFKA_BROKER_URL", "127.0.0.1:9092")

        # Tabela de despacho de ações. Facilita "completar" o listener: para uma
        # ação nova, basta registrar um handler aqui.
        self._handlers = {
            "REPLICATE_CHUNK": self._handle_replicate,
        }

        # Sistema de retries (tenta 5 vezes antes de falhar)
        max_retries = 5
        for attempt in range(max_retries):
            try:
                self.consumer = KafkaConsumer(
                    self.topic,
                    bootstrap_servers=[self.broker_url],
                    value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                    auto_offset_reset='latest'
                )
                break  # Conectou com sucesso, sai do loop!
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"[{self.node_id}] Kafka ainda não pronto. Tentando de novo em 3s...")
                    time.sleep(3)
                else:
                    raise e  # Falhou todas as vezes, levanta o erro

    def start(self) -> None:
        """Inicia a escuta do Kafka em uma thread em background."""
        print(f"[{self.node_id}] [Kafka] Escutando comandos em '{self.topic}' (Broker: {self.broker_url})...")
        thread = threading.Thread(target=self._listen_loop, daemon=True)
        thread.start()

    def _listen_loop(self) -> None:
        for message in self.consumer:
            try:
                command = message.value
                action = command.get('action')
                handler = self._handlers.get(action)
                if handler is None:
                    print(f"[{self.node_id}] [Kafka] Comando desconhecido: {action}")
                    continue
                handler(command)
            except Exception as e:
                print(f"[{self.node_id}] [Kafka] Erro no processamento da mensagem: {e}")

    def _transfer_chunk(self, chunk_id: str, target_node_id: str) -> bool:
        """
        Copia um chunk LOCAL para o nó de destino via gRPC (StoreChunk, client-streaming).

        A simulação de atraso de rede agora vem do helper compartilhado net_sim
        (mesmo NETWORK_DELAY usado no fan-out do upload e no failover do download).
        """
        try:
            # 1. Valida se o chunk existe localmente
            if chunk_id not in self.storage.list_chunk_ids():
                print(f"[{self.node_id}] [Kafka] Chunk '{chunk_id}' não encontrado localmente.")
                return False

            # Simulação de atraso de rede (centralizada em net_sim).
            apply_network_delay(context="rereplicacao", node_id=self.node_id)

            data = self.storage.read_chunk(chunk_id)
            target_node = self.registry.get(target_node_id)

            # Deriva chunk_index e upload_id do chunk_id "<upload_id>_chunk_<idx>".
            if "_chunk_" in chunk_id:
                upload_id, _, idx_str = chunk_id.rpartition("_chunk_")
                chunk_index = int(idx_str) if idx_str.isdigit() else 0
            else:
                upload_id, chunk_index = chunk_id, 0

            # 2. Transfere via gRPC (client-streaming correto, em STREAM_SIZE)
            cli = ReplicationClient(target_node.host, target_node.port)
            try:
                resp = cli.store_chunk(
                    chunk_id=chunk_id,
                    chunk_index=chunk_index,
                    upload_id=upload_id,
                    origin_node_id=self.node_id,
                    data=data,
                )
            finally:
                cli.close()

            if not getattr(resp, "ok", False):
                print(f"[{self.node_id}] [Kafka] Destino recusou o chunk '{chunk_id}': {resp.message}")
                return False
            return True
        except Exception as e:
            print(f"[{self.node_id}] [Kafka] Erro na transferência via gRPC do chunk '{chunk_id}': {e}")
            return False

    def _handle_replicate(self, command: dict) -> None:
        chunk_id = command.get('chunk_id')
        target_node_id = command.get('target_node_id')      # DESTINO
        removed_node_id = command.get('removed_node_id')    # nó morto/drenado (fecha o ciclo)
        if not chunk_id or not target_node_id:
            return

        print(f"[{self.node_id}] [Kafka] Replicando '{chunk_id}' -> '{target_node_id}'...")
        if self._transfer_chunk(chunk_id, target_node_id):
            print(f"[{self.node_id}] [Kafka] Replicação de '{chunk_id}' concluída.")

            # Telemetria (best-effort): alimenta o telemetry_hub.
            emit_event(
                "rereplication_applied",
                {
                    "chunk_id": chunk_id,
                    "source": self.node_id,
                    "destiny": target_node_id,
                    "removed": removed_node_id,
                },
            )

            # Fecha o ciclo: coordenador troca o nó morto/drenado pelo novo (idempotente).
            if removed_node_id:
                try:
                    client = ControlClient()
                    client.update_chunk_replicas(chunk_id, target_node_id, removed_node_id)
                    client.close()
                except Exception as e:
                    print(f"[{self.node_id}] [Kafka] Falha ao atualizar metadados de '{chunk_id}': {e}")
