"""
Adaptador entre o ReplicationWatcher (plano de controle) e o Kafka.
O watcher chama publisher(event). Este adaptador traduz cada chunk perdido em um
comando REPLICATE_CHUNK publicado no tópico do nó-fonte (réplica viva) que executa
a cópia via StoreChunk. O nó-morto viaja no comando para o nó-fonte fechar o ciclo
chamando UpdateChunkReplicas depois de copiar.

Além do comando crítico, publica um EVENTO de telemetria 'rereplication_issued'
para o telemetry_hub montar a visão do sistema (não crítico; não afeta a cópia).
"""

from __future__ import annotations
from dfs.cluster.kafka_publisher import ClusterEventPublisher


class KafkaRereplicationPublisher:
    def __init__(self, broker_url: str | None = None):
        self._pub = ClusterEventPublisher(broker_url)

    def __call__(self, event: dict) -> None:
        dead_node = event["node_id"]
        for lost in event.get("lost_chunks", []):
            chunk_id = lost["chunk_id"]
            destiny = lost.get("destiny")  # nó-destino (recebe a cópia)
            alive_rep = lost.get("alive_rep", [])  # réplicas vivas (fontes possíveis)

            if destiny is None:
                print(f"[rerepl] {chunk_id}: sem destino livre; adiado.")
                continue
            if not alive_rep:
                print(f"[rerepl] {chunk_id}: sem réplica viva como fonte; adiado.")
                continue

            source_node = alive_rep[0]  # qualquer réplica viva serve de fonte
            command = {
                "action": "REPLICATE_CHUNK",
                "chunk_id": chunk_id,
                "target_node_id": destiny,
                "removed_node_id": dead_node,  # nó morto, para fechar o ciclo
            }
            # Comando crítico (propaga erro): o nó-fonte lê local e copia p/ o destino.
            self._pub.publish_storage_command(source_node, command)

            # Telemetria (best-effort): alimenta o telemetry_hub sem afetar a cópia.
            self._pub.publish_event(
                "rereplication_issued",
                {
                    "chunk_id": chunk_id,
                    "dead_node": dead_node,
                    "source": source_node,
                    "destiny": destiny,
                },
            )
