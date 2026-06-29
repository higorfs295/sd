# dfs/cluster/kafka_publisher.py
import json
import os
import time
from kafka import KafkaProducer


class ClusterEventPublisher:
    """
    Publica eventos no Kafka. Tem dois usos hoje:

      1. COMANDOS DE RE-REPLICAÇÃO (crit):
         `publish_storage_command(...)` envia ordens REPLICATE_CHUNK ao tópico
         do Storage Node fonte. Se isso falhar, a re-replicação não acontece,
         então aqui o erro é PROPAGADO (o watcher tenta de novo no próximo ciclo).

      2. MÉTRICAS DE TELEMETRIA (observabilidade, não crítico):
         `publish_metric(...)` envia tempos de operação ao tópico 'cluster-metrics',
         consumido pelo telemetry_hub.py. Aqui o erro é ENGOLIDO de propósito: se o
         Kafka não estiver no ar, quem chama (ex.: o benchmark) roda do mesmo jeito.

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

    # Tópico onde o telemetry_hub.py escuta as métricas de performance.
    METRICS_TOPIC = "cluster-metrics"

    def __init__(self, broker_url: str = None):
        self.broker_url = broker_url or os.getenv("KAFKA_BROKER_URL", "localhost:9092")
        # Conexao preguicosa: o produtor so e criado no primeiro publish.
        self._producer = None
        # Se a publicacao de metricas falhar uma vez (Kafka fora), desliga a
        # telemetria nesta execucao para nao repetir a espera de conexao a cada
        # chamada (importante para o benchmark nao travar metrica a metrica).
        self._metrics_disabled = False

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

    def publish_metric(self, operation: str, duration_seconds: float, extra: dict = None) -> None:
        """
        Publica uma metrica de performance no topico 'cluster-metrics'.

        O telemetry_hub.py espera, por mensagem, um JSON com pelo menos:
            - "operation":        str    (ex.: "upload", "download")
            - "duration_seconds": float  (tempo da operacao em segundos)
        Chaves extras (ex.: tamanho_mb, nos_ativos) sao ignoradas pelo hub atual,
        mas vao junto para uso futuro / depuracao.

        TOLERANTE A FALHA DE PROPOSITO: metrica e observabilidade, nao dado critico.
        Se o Kafka nao estiver no ar, esta funcao NUNCA quebra nem trava quem chama
        (o benchmark roda igual). No primeiro fracasso ela se autodesliga, para nao
        repetir a espera de conexao a cada chamada.
        """
        if self._metrics_disabled:
            return

        evento = {
            "operation": str(operation),
            "duration_seconds": float(duration_seconds),
        }
        if extra:
            evento.update(extra)

        try:
            # Poucas tentativas: a metrica nao pode segurar o benchmark.
            producer = self._get_producer(max_retries=1, delay=0.0)
            producer.send(self.METRICS_TOPIC, value=evento)
            producer.flush()
        except Exception as exc:
            self._metrics_disabled = True
            print(
                f"[Publisher] (metrica) Kafka indisponivel — telemetria desativada "
                f"nesta execucao: {exc}"
            )

    def close(self) -> None:
        """Fecha o produtor com flush, se ele chegou a ser criado. Idempotente."""
        if self._producer is not None:
            try:
                self._producer.flush()
                self._producer.close()
            except Exception:
                pass
            finally:
                self._producer = None