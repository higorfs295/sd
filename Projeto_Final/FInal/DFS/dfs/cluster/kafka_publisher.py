# dfs/cluster/kafka_publisher.py
import json
import os
import time
from kafka import KafkaProducer


class ClusterEventPublisher:
    """
    Publica comandos no Kafka (ex.: REPLICATE_CHUNK na re-replicação).

    CONEXÃO PREGUIÇOSA (correção importante):
        Antes, o KafkaProducer era criado já no __init__. Isso fazia o coordenador
        QUEBRAR na subida quando o broker Kafka ainda não estava pronto — a porta
        9092 abre antes de o broker aceitar conexões. O server.py então caía no
        publisher de fallback (_pub_default), que só IMPRIME "[watcher] Pedido de
        re-replicacao produzido" e NUNCA publica no Kafka. Resultado: a
        re-replicacao nunca saia do papel (replica nova jamais aparecia nos
        metadados), mesmo com todo o resto funcionando.

        Agora a conexao e adiada para o PRIMEIRO envio de verdade. Como o primeiro
        envio so acontece quando um no morre (com o cluster ja no ar ha um tempo),
        o broker ja esta pronto, e o coordenador sempre usa o publisher real.
    """

    def __init__(self, broker_url: str = None):
        self.broker_url = broker_url or os.getenv("KAFKA_BROKER_URL", "localhost:9092")
        # Conexao preguicosa: o produtor so e criado no primeiro publish.
        self._producer = None

    def _get_producer(self, max_retries: int = 5, delay: float = 3.0):
        """
        Cria o KafkaProducer uma unica vez. Se o broker ainda nao responder,
        tenta de novo algumas vezes (mesma resiliencia do consumidor dos nos),
        em vez de falhar de imediato.
        """
        if self._producer is not None:
            return self._producer

        ultimo_erro = None
        for tentativa in range(max_retries):
            try:
                self._producer = KafkaProducer(
                    bootstrap_servers=[self.broker_url],
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                    retries=5,
                )
                return self._producer
            except Exception as exc:  # broker ainda nao pronto, etc.
                ultimo_erro = exc
                if tentativa < max_retries - 1:
                    print(
                        f"[Publisher] Kafka ainda nao pronto ({self.broker_url}); "
                        f"tentando de novo em {delay:.0f}s..."
                    )
                    time.sleep(delay)
        # Esgotou as tentativas: propaga o erro para o chamador (o loop do watcher
        # registra e tenta de novo no proximo ciclo).
        raise ultimo_erro

    def publish_storage_command(self, target_node_id: str, command: dict) -> None:
        """Manda uma ordem (ex.: REPLICATE_CHUNK) ao topico do Storage Node fonte."""
        topic = f"storage-node-{target_node_id}-commands"
        producer = self._get_producer()
        producer.send(topic, value=command)
        producer.flush()
        print(f"[Publisher] Comando enviado para '{topic}': {command['action']}")