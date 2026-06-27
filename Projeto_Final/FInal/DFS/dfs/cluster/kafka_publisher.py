# dfs/cluster/kafka_publisher.py
import json
import os
from kafka import KafkaProducer


class ClusterEventPublisher:
    def __init__(self, broker_url: str = None):
        self.broker_url = broker_url or os.getenv("KAFKA_BROKER_URL", "localhost:9092")
        self.producer = KafkaProducer(
            bootstrap_servers=[self.broker_url],
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )

    def publish_storage_command(self, target_node_id: str, command: dict) -> None:
        """Usado pelo plano de re-replicação para mandar ordens (REPLICATE_CHUNK) ao Storage Node fonte."""
        topic = f"storage-node-{target_node_id}-commands"
        self.producer.send(topic, value=command)
        self.producer.flush()
        print(f"[Publisher] Comando enviado para '{topic}': {command['action']}")
