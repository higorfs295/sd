# dfs/cluster/replication_watcher.py
"""
Thread de fundo no coordenador que acorda periodicamente, varre o NodeRegistry e detecta o instante em que um nó TRANSITA para DEAD.
Para cada morte recém-detectada, descobre nos metadados quais chunks o nó morto guardava e quantas réplicas ainda estão vivas, e emite um "pedido de re-replicação".

Este watcher NÃO conhece Kafka. Ele entrega um evento (dict) a um publisher, que irá publicar esse evento.
O contrato entre os dois é o formato do evento:
    {
        "node_id": "node3",
        "detected_at":  "2025-06-11T14:03:22.100000",
        "lost_chunks": [
            {"chunk_id": "...", "alive_rep": ["node1", "node2"], "destiny": None},
            ...
        ],
    }
"""

from __future__ import annotations

import datetime
import threading
import time

from dfs.config import WATCHER_INTERVAL

# Reusamos a enum NodeStatus do .proto (ALIVE/SUSPECT/DEAD)
from dfs.pb import dfs_pb2


def _pub_default(event: dict) -> None:
    """
    Publisher default para teste ISOLADO do plano de controle (sem Kafka).
    Apenas imprime o pedido. Em produção, implementa uma saída que publica o evento no tópico Kafka 'node-dead'.
    """
    print(f"[watcher] Pedido de re-replicação produzido: {event}")


class ReplicationWatcher:
    def __init__(
        self,
        registry,  # estado vivo dos nós, classificação ALIVE/SUSPECT/DEAD
        metadata,  # mapa arquivo -> chunks -> réplicas
        publisher=_pub_default,  # função que recebe o evento (dict) para publicação no Kafka
        interval: float = WATCHER_INTERVAL,
    ):
        self.registry = registry
        self.metadata = metadata
        self.publisher = publisher
        self.interval = interval

        # MEMÓRIA DO WATCHER: o último status que o watcher viu para cada nó
        # É o que permite detectar a transição para o status DEAD, já que o NodeRegistry calcula o status na hora e NÃO guarda histórico
        # Formato: node_id -> último NodeStatus visto
        self._last_status: dict[str, int] = {}

        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Sobe a thread daemon de varredura (morre junto com o coordenador)."""
        self._thread = threading.Thread(
            target=self._loop, name="replication-watcher", daemon=True
        )
        self._thread.start()
        print(f"[watcher] Varredura ativa de nós iniciada (intervalo={self.interval}s)")

    def _loop(self) -> None:
        """ "Loop interno da thread que repete as ações de dormir, acordar e varrer."""
        while True:
            time.sleep(
                self.interval
            )  # dorme entre o WATCHER_INTERVAL, para não sobrecarregar de processos
            try:
                self._watch()
            except Exception as exc:  # noqa: BLE001
                # Uma falha numa varredura não pode derrubar a thread: logamos e seguimos no próximo ciclo (a re-replicação é eventual)
                print(f"[watcher] Erro na varredura: {exc}")

    # Detecção
    def _watch(self) -> None:
        """
        Um ciclo de varredura:
        1. tira um snapshot do status de todos os nós canônicos;
        2. detecta quem transitou para DEAD desde o último ciclo;
        3. atualiza a memória (resetando recuperações DEAD->ALIVE);
        4. processa cada morte recém-detectada.
        """
        actual_status = self._snapshot_status()

        recent_dead = []
        for node_id, status in actual_status.items():
            previous = self._last_status.get(node_id)
            # Primeira vez que vemos o nó (anterior is None): apenas anotamos na memória, sem disparar.
            # Isso evita o falso positivo de inicialização: um nó que nunca bateu lê como DEAD (last_heartbeat=0.0), mas não houve uma morte de verdade, então não há o que re-replicar.
            if (
                previous is not None
                and previous != dfs_pb2.NODE_STATUS_DEAD
                and status == dfs_pb2.NODE_STATUS_DEAD  # pega a transição para DEAD
            ):
                recent_dead.append(node_id)

        # Atualiza a memória depois de comparar
        self._last_status = actual_status

        for node_id in recent_dead:
            self._process_dead(node_id, actual_status)

    def _snapshot_status(self) -> dict[str, int]:
        """
        Status de TODOS os nós da membership canônica, neste instante.

        Observação: status_of pega o lock por nó, então o snapshot NÃO é perfeito entre nós.
        Para detecção de borda isso é aceitável: uma eventual inconsistência momentânea é corrigida no ciclo seguinte.
        """
        return {
            info.node_id: self.registry.status_of(info.node_id)
            for info in self.registry.canonical_members()
        }

    # Montagem
    def _process_dead(self, dead_node: str, actual_status: dict[str, int]) -> None:
        """Monta e entrega o pedido de re-replicação para um nó recém-morto."""
        lost_chunks = self._lost_chunks(dead_node, actual_status)

        if not lost_chunks:
            # O nó morreu mas não guardava nenhum chunk segundo os metadados.
            # Nada a re-replicar, mas ainda assim logamos a transição.
            print(f"[watcher] {dead_node}: status DEAD; nenhum chunk afetado.")
            return

        event = {
            "node_id": dead_node,
            # detected_at usa relógio normal, pois é um carimbo humano/log do evento.
            # NÃO usamos time.monotonic() aqui: monotonic serve para medir silêncio (intervalos), não para datar eventos.
            "detected_at": datetime.datetime.now().isoformat(),
            "lost_chunks": lost_chunks,
        }

        print(
            f"[watcher] {dead_node}: status DEAD; "
            f"{len(lost_chunks)} chunk(s) com réplica perdida."
        )
        # Entrega a informação ao publisher do evento.
        self.publisher(event)

    def _lost_chunks(self, dead_node: str, actual_status: dict[str, int]) -> list[dict]:
        """Varre os metadados e devolve os chunks que tinham réplica no nó morto, cada um com a lista de réplicas que CONTINUAM vivas."""
        lost = []
        for path in self.metadata.list_files():
            info = self.metadata.get_file(path)
            if info is None:
                continue  # removido entre o list e o get
            for chunk in info.get("chunks", []):
                rep = chunk.get("replicas", [])
                if dead_node not in rep:
                    continue  # este chunk não estava no nó morto
                # Réplicas que sobraram E estão vivas agora (exclui o morto).
                alive_rep = [
                    nid
                    for nid in rep
                    if nid != dead_node
                    and actual_status.get(nid) == dfs_pb2.NODE_STATUS_ALIVE
                ]
                lost.append(
                    {
                        "chunk_id": chunk["chunk_id"],
                        "alive_rep": alive_rep,
                        # Destino da cópia: definido no DIA 13/06. Por ora, None.
                        # alive_rep == [] sinaliza chunk SEM fonte viva: o
                        # consumer não tem de onde copiar agora, é um
                        # chunk temporariamente indisponível, tratado à parte.
                        "destiny": self._select_destiny(
                            chunk, alive_rep, actual_status
                        ),
                    }
                )
        return lost

    def _select_destiny(self, chunk, alive_rep, actual_status):
        """
        STUB (dia 13). Critério futuro: nó vivo com maior free_space_bytes que
        ainda NÃO guarda este chunk (free_space_bytes já vem no heartbeat).
        Por enquanto devolve None, para manter ESTE passo focado só na detecção.
        """
        return None
