"""
SISTEMA DE ARQUIVOS DISTRIBUÍDO (DFS) - HUB DE TELEMETRIA KAFKA
==============================================================
Consumidor Kafka que observa o SISTEMA INTEIRO (o máximo que a instrumentação
alimenta), não só durações de upload/download.

Consome DOIS tópicos:
  - 'cluster-metrics' : durações de operação {operation, duration_seconds, ...}
  - 'cluster-events'  : eventos do sistema {event, ts, ...} publicados por
                        HeartbeatWorker (heartbeat), data_service (upload/download),
                        HeartbeatWorker/GC (gc_delete), rereplication_publisher
                        (rereplication_issued), kafka_listener (rereplication_applied),
                        DrainManager (drain_started/completed/timeout).

O painel mostra, ao vivo:
  - por operação: contagem, mínimo, máximo, média (das durações);
  - por nó: última vez visto (heartbeat), espaço livre, nº de chunks;
  - contadores de eventos do sistema (gc, re-replicação, drenagem...);
  - as últimas ocorrências relevantes.

Se apenas um broker estiver no ar, ele conecta no que responder.
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict

from kafka import KafkaConsumer

KAFKA_BROKERS = os.getenv("KAFKA_BROKERS", "127.0.0.1:9092,127.0.0.1:9093").split(",")
TOPIC_METRICS = "cluster-metrics"
TOPIC_EVENTS = "cluster-events"
REFRESH_S = 1.0  # intervalo de redesenho do painel


def _fmt_bytes(n: float) -> str:
    for unidade in ["B", "KB", "MB", "GB", "TB"]:
        if abs(n) < 1024:
            return f"{n:.1f} {unidade}"
        n /= 1024
    return f"{n:.1f} PB"


class EstadoTelemetria:
    def __init__(self):
        # operacao -> {count, sum, min, max}
        self.ops = defaultdict(lambda: {"count": 0, "sum": 0.0, "min": float("inf"), "max": 0.0})
        # node_id -> {last_seen, free_space, chunks, intervalo}
        self.nos = {}
        # tipo de evento -> contagem
        self.eventos = defaultdict(int)
        # buffer das últimas linhas de log de eventos
        self.recentes = []

    def registrar_metrica(self, dados: dict) -> None:
        op = dados.get("operation", "UNKNOWN")
        dur = float(dados.get("duration_seconds", 0.0))
        e = self.ops[op]
        e["count"] += 1
        e["sum"] += dur
        e["min"] = min(e["min"], dur)
        e["max"] = max(e["max"], dur)

    def registrar_evento(self, dados: dict) -> None:
        tipo = dados.get("event", "UNKNOWN")
        self.eventos[tipo] += 1
        agora = time.time()

        if tipo == "heartbeat":
            nid = dados.get("node_id", "?")
            self.nos[nid] = {
                "last_seen": agora,
                "free_space": dados.get("free_space_bytes"),
                "chunks": dados.get("chunks"),
                "intervalo": dados.get("intervalo_real_s"),
            }
        else:
            # Guarda uma linha legível dos eventos não-heartbeat mais interessantes.
            resumo = ", ".join(
                f"{k}={v}" for k, v in dados.items() if k not in ("event", "ts")
            )
            self.recentes.append((agora, tipo, resumo))
            self.recentes = self.recentes[-8:]  # só as últimas 8

    def render(self) -> None:
        # Limpa a tela (ANSI). Em terminais que não suportam, apenas imprime abaixo.
        print("\033[2J\033[H", end="")
        print("=" * 78)
        print("📈 HUB DE TELEMETRIA DFS — visão do sistema inteiro")
        print("=" * 78)

        print("\n[ OPERAÇÕES ]  (durações, em segundos)")
        if not self.ops:
            print("  (aguardando métricas de upload/download...)")
        for op, e in sorted(self.ops.items()):
            media = e["sum"] / e["count"] if e["count"] else 0.0
            minimo = e["min"] if e["min"] != float("inf") else 0.0
            print(
                f"  {op:<12} n={e['count']:<5} min={minimo:.4f}  "
                f"max={e['max']:.4f}  média={media:.4f}"
            )

        print("\n[ NÓS ]  (via evento de heartbeat)")
        if not self.nos:
            print("  (aguardando heartbeats dos nós...)")
        agora = time.time()
        for nid in sorted(self.nos):
            info = self.nos[nid]
            idade = agora - info["last_seen"]
            saude = "🟢 ALIVE" if idade < 5 else ("🟡 SUSPECT" if idade < 20 else "🔴 DEAD")
            livre = _fmt_bytes(info["free_space"]) if info["free_space"] is not None else "?"
            chunks = info["chunks"] if info["chunks"] is not None else "?"
            interv = f"{info['intervalo']:.2f}s" if info["intervalo"] is not None else "?"
            print(
                f"  {nid:<8} {saude:<11} visto há {idade:4.1f}s | "
                f"livre={livre:<10} chunks={chunks:<5} intervalo={interv}"
            )

        print("\n[ EVENTOS DO SISTEMA ]  (contadores)")
        if not self.eventos:
            print("  (nenhum evento ainda)")
        for tipo, qtd in sorted(self.eventos.items()):
            if tipo == "heartbeat":
                continue
            print(f"  {tipo:<24} {qtd}")

        if self.recentes:
            print("\n[ ÚLTIMOS EVENTOS ]")
            for ts, tipo, resumo in self.recentes:
                hhmmss = time.strftime("%H:%M:%S", time.localtime(ts))
                print(f"  {hhmmss}  {tipo:<22} {resumo}")

        print("\n" + "=" * 78)
        print("Ctrl+C para sair.")


def main() -> None:
    print(f"Conectando ao(s) broker(s): {', '.join(KAFKA_BROKERS)}...")
    try:
        consumer = KafkaConsumer(
            TOPIC_METRICS,
            TOPIC_EVENTS,
            bootstrap_servers=KAFKA_BROKERS,
            value_deserializer=lambda x: json.loads(x.decode("utf-8")),
            auto_offset_reset="latest",
            consumer_timeout_ms=int(REFRESH_S * 1000),  # acorda para redesenhar
        )
    except Exception as e:
        print(f"🚨 [ERRO] Falha ao conectar ao Kafka: {e}")
        return

    estado = EstadoTelemetria()
    ultimo_render = 0.0
    print("🟩 Hub online. Aguardando telemetria...\n")

    try:
        while True:
            # consumer_timeout_ms faz o for retornar periodicamente mesmo sem mensagens.
            for message in consumer:
                dados = message.value
                if message.topic == TOPIC_METRICS:
                    estado.registrar_metrica(dados)
                else:
                    estado.registrar_evento(dados)
                if time.time() - ultimo_render >= REFRESH_S:
                    estado.render()
                    ultimo_render = time.time()
            # Sem mensagens no intervalo: redesenha para atualizar idades/saúde.
            estado.render()
            ultimo_render = time.time()
    except KeyboardInterrupt:
        print("\n🛑 Hub de telemetria encerrado.")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
