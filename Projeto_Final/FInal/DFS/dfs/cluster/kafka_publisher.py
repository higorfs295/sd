# dfs/cluster/kafka_publisher.py
import json
import os
from kafka import KafkaProducer

class ClusterEventPublisher:
    def __init__(self, broker_url: str = None):
        self.broker_url = broker_url or os.getenv("KAFKA_BROKER_URL", "localhost:9092")
        self.producer = KafkaProducer(
            bootstrap_servers=[self.broker_url],
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )

    def publish_node_dead(self, dead_node_id: str) -> None:
        """
        Publica um evento avisando que um nó caiu.
        A Vitória vai chamar isso: publisher.publish_node_dead("node2")
        """
        event = {
            "event_type": "NODE_DEAD",
            "node_id": dead_node_id
        }
        self.producer.send("node-dead", value=event)
        self.producer.flush()
        print(f"[Publisher] Evento disparado no tópico 'node-dead': {event}")

    def publish_storage_command(self, target_node_id: str, command: dict) -> None:
        """Usado pelo ReplicationManager para mandar ordens para os Storage Nodes."""
        topic = f"storage-node-{target_node_id}-commands"
        self.producer.send(topic, value=command)
        self.producer.flush()
        print(f"[Publisher] Comando enviado para '{topic}': {command['action']}")