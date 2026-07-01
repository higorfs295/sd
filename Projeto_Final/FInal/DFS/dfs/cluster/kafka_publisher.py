# dfs/cluster/kafka_publisher.py
import json
import os
import time
import threading
from kafka import KafkaProducer


class ClusterEventPublisher:
    """
    Publica no Kafka. Três fluxos e dois tópicos:

      1. COMANDOS DE RE-REPLICAÇÃO (crítico) -> tópico do nó-fonte:
         publish_storage_command(...) envia REPLICATE_CHUNK ao tópico
         'storage-node-<id>-commands'. Erro é PROPAGADO (o watcher tenta de novo).

      2. MÉTRICAS DE TELEMETRIA (não crítico) -> METRICS_TOPIC:
         publish_metric(...) envia durações de operação, consumidas pelo
         telemetry_hub.py. Erro é ENGOLIDO (o benchmark roda igual).

      3. EVENTOS DE TELEMETRIA (não crítico) -> EVENTS_TOPIC:
         publish_event(...) envia eventos do sistema (heartbeat, gc, re-replicação,
         morte de nó...) para o telemetry_hub montar a visão geral.
         Erro é ENGOLIDO.

    CONEXÃO PREGUIÇOSA: o KafkaProducer só é criado no PRIMEIRO envio de verdade.
    Assim o coordenador não quebra na subida se o broker ainda não aceita conexões
    (a porta 9092 abre antes de o broker estar pronto).
    """

    METRICS_TOPIC = "cluster-metrics"
    EVENTS_TOPIC = "cluster-events"

    def __init__(self, broker_url: str = None):
        self.broker_url = broker_url or os.getenv("KAFKA_BROKER_URL", "localhost:9092")
        self._producer = None
        # Telemetria (métricas + eventos) se autodesliga na 1ª falha, para não
        # repetir a espera de conexão a cada chamada (não trava o benchmark/nós).
        self._telemetry_disabled = False
        self._lock = threading.Lock()

    def _get_producer(self, max_retries: int = 5, delay: float = 3.0):
        """Cria o KafkaProducer uma única vez, com retries. Propaga erro se esgotar."""
        if self._producer is not None:
            return self._producer
        with self._lock:
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
                except Exception as exc:
                    ultimo_erro = exc
                    if tentativa < max_retries - 1:
                        print(
                            f"[Publisher] Kafka ainda nao pronto ({self.broker_url}); "
                            f"tentando de novo em {delay:.0f}s..."
                        )
                        time.sleep(delay)
            raise ultimo_erro

    # ---------------------------------------------------------- CRÍTICO
    def publish_storage_command(self, target_node_id: str, command: dict) -> None:
        """Ordem (ex.: REPLICATE_CHUNK) ao tópico de comandos do Storage Node fonte."""
        topic = f"storage-node-{target_node_id}-commands"
        producer = self._get_producer()
        producer.send(topic, value=command)
        producer.flush()
        print(f"[Publisher] Comando enviado para '{topic}': {command.get('action')}")

    # ---------------------------------------------------- NÃO CRÍTICO
    def publish_metric(self, operation: str, duration_seconds: float, extra: dict = None) -> None:
        """
        Métrica de performance no tópico 'cluster-metrics'. Formato mínimo:
            {"operation": str, "duration_seconds": float, ...extras}
        Tolerante a falha: nunca quebra nem trava quem chama.
        """
        if self._telemetry_disabled:
            return
        evento = {
            "operation": str(operation),
            "duration_seconds": float(duration_seconds),
            "ts": time.time(),
        }
        if extra:
            evento.update(extra)
        self._best_effort_send(self.METRICS_TOPIC, evento)

    def publish_event(self, event_type: str, payload: dict = None) -> None:
        """
        Evento do sistema no tópico 'cluster-events' (heartbeat, gc, rereplicacao,
        drain, node_dead, ...). Tolerante a falha, igual às métricas.
        """
        if self._telemetry_disabled:
            return
        evento = {"event": str(event_type), "ts": time.time()}
        if payload:
            evento.update(payload)
        self._best_effort_send(self.EVENTS_TOPIC, evento)

    def _best_effort_send(self, topic: str, evento: dict) -> None:
        try:
            producer = self._get_producer(max_retries=1, delay=0.0)
            producer.send(topic, value=evento)
            producer.flush()
        except Exception as exc:
            self._telemetry_disabled = True
            print(
                f"[Publisher] (telemetria) Kafka indisponivel — telemetria "
                f"desativada nesta execucao: {exc}"
            )

    def close(self) -> None:
        """Fecha o produtor com flush, se criado. Idempotente."""
        if self._producer is not None:
            try:
                self._producer.flush()
                self._producer.close()
            except Exception:
                pass
            finally:
                self._producer = None


# ----------------------------------------------------------------------------
# INSTÂNCIA COMPARTILHADA (por processo)
#
# Permite que qualquer componente (HeartbeatWorker, data_service, kafka_listener,
# rereplication_publisher) emita telemetria em UMA linha, reusando um único
# produtor por processo, sem precisar carregar um publisher por toda parte.
# ----------------------------------------------------------------------------
_shared_publisher: ClusterEventPublisher | None = None
_shared_lock = threading.Lock()


def get_shared_publisher() -> ClusterEventPublisher:
    """Devolve o publisher compartilhado do processo, criando-o na primeira vez."""
    global _shared_publisher
    if _shared_publisher is None:
        with _shared_lock:
            if _shared_publisher is None:
                _shared_publisher = ClusterEventPublisher()
    return _shared_publisher


def emit_metric(operation: str, duration_seconds: float, extra: dict = None) -> None:
    """Atalho best-effort para publicar uma métrica sem carregar o publisher."""
    try:
        get_shared_publisher().publish_metric(operation, duration_seconds, extra)
    except Exception:
        pass


def emit_event(event_type: str, payload: dict = None) -> None:
    """Atalho best-effort para publicar um evento sem carregar o publisher."""
    try:
        get_shared_publisher().publish_event(event_type, payload)
    except Exception:
        pass
