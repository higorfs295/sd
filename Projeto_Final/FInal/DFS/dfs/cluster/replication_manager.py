# dfs/cluster/replication_manager.py
import json
import threading
import os
from kafka import KafkaConsumer
from .kafka_publisher import ClusterEventPublisher

# Nota: Você precisará importar a classe que acessa o catálogo da Vitória
# Exemplo hipotético: from dfs.cluster.catalog import CatalogManager

class ReplicationManager:
    def __init__(self, catalog_manager, broker_url: str = None):
        self.catalog = catalog_manager # Acesso aos metadados da Vitória
        self.broker_url = broker_url or os.getenv("KAFKA_BROKER_URL", "localhost:9092")
        self.publisher = ClusterEventPublisher(self.broker_url)
        
        self.consumer = KafkaConsumer(
            "node-dead",
            bootstrap_servers=[self.broker_url],
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            auto_offset_reset='latest'
        )

    def start(self) -> None:
        print("[ReplicationManager] Iniciando escuta de anomalias no tópico 'node-dead'...")
        threading.Thread(target=self._listen_for_dead_nodes, daemon=True).start()

    def _listen_for_dead_nodes(self) -> None:
        for message in self.consumer:
            event = message.value
            if event.get("event_type") == "NODE_DEAD":
                dead_node_id = event.get("node_id")
                print(f"[ReplicationManager] ALERTA: Detectada queda do {dead_node_id}. Iniciando plano de contingência...")
                self._trigger_rereplication(dead_node_id)

    def _trigger_rereplication(self, dead_node_id: str) -> None:
        """Lógica de contingência para re-replicar chunks perdidos."""
        
        # 1. Descobre quais chunks estavam no nó morto (Requer integração com o catálogo da Vitória)
        # chunks_afetados = self.catalog.get_chunks_on_node(dead_node_id)
        
        # MOCK PROVISÓRIO (Para você poder testar a comunicação agora):
        chunks_afetados = ["chunk_xyz_1", "chunk_abc_2"]
        print(f"[ReplicationManager] Chunks afetados pela queda: {chunks_afetados}")

        for chunk_id in chunks_afetados:
            # 2. Descobre quem ainda tem uma cópia viva desse chunk
            # source_node = self.catalog.get_node_with_chunk(chunk_id, exclude=dead_node_id)
            source_node = "node1" # Mock

            # 3. Escolhe um novo nó para receber a nova cópia
            # target_node = self.catalog.get_best_node_for_chunk()
            target_node = "node3" # Mock

            if source_node and target_node:
                # 4. Monta a ordem e publica no Kafka para o Storage Node executar
                command = {
                    "action": "REPLICATE_CHUNK",
                    "chunk_id": chunk_id,
                    "target_node_id": target_node
                }
                self.publisher.publish_storage_command(source_node, command)
                print(f"[ReplicationManager] Ordem de re-replicação enviada: {source_node} -> {target_node} ({chunk_id})")
            else:
                print(f"[ReplicationManager] ERRO CRÍTICO: Não há cópias vivas ou nós disponíveis para o chunk {chunk_id}!")