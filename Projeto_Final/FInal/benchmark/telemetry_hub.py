"""
SISTEMA DE ARQUIVOS DISTRIBUÍDO (DFS) - HUB DE TELEMETRIA KAFKA
==============================================================
Descrição Geral:
    Consumidor Kafka resiliente dedicado à coleta de métricas de performance. 
    Conecta-se a múltiplos Brokers para garantir alta disponibilidade na leitura.

Refatoração Sênior:
    - Adicionado suporte a cluster de Brokers (9092 e 9093).
    - Motor de estatísticas integrado (Mínimo, Máximo, Média Móvel).
"""

from __future__ import annotations

import json
from kafka import KafkaConsumer

# --- CONFIGURAÇÃO DE ALTA DISPONIBILIDADE ---
# Aponta para a lista de brokers do cluster. Se o primeiro cair, ele tenta o segundo.
KAFKA_BROKERS = ["127.0.0.1:9092", "127.0.0.1:9093"]
TOPIC_METRICS = "cluster-metrics"
# --------------------------------------------

def renderizar_painel(operacao: str, duracao: float, estatisticas: dict) -> None:
    """Formata e imprime as métricas de forma visual e tabulada no terminal."""
    qtd = estatisticas['count']
    media = estatisticas['sum'] / qtd
    minimo = estatisticas['min']
    maximo = estatisticas['max']
    
    print(f"[{operacao:^10}] ⏱️ Atual: {duracao:.4f}s | 📉 Min: {minimo:.4f}s | 📈 Max: {maximo:.4f}s | 📊 Média: {media:.4f}s")

def main() -> None:
    print(f"\n{'='*75}")
    print("📈 HUB DE TELEMETRIA: Monitoramento Ativo (Cluster Kafka)")
    print(f"   -> Conectando aos brokers: {', '.join(KAFKA_BROKERS)}")
    print(f"{'='*75}\n")

    try:
        consumer = KafkaConsumer(
            TOPIC_METRICS,
            bootstrap_servers=KAFKA_BROKERS,
            value_deserializer=lambda x: json.loads(x.decode('utf-8')),
            auto_offset_reset='latest'
        )
        print(f"🟩 Hub online e escutando métricas no tópico '{TOPIC_METRICS}'...\n")
    except Exception as e:
        print(f"🚨 [ERRO CRÍTICO] Falha ao conectar ao cluster Kafka: {e}")
        return

    # Motor de armazenamento de estado (Evita listas gigantescas estourando a memória)
    stats_globais = {
        "count": 0,
        "sum": 0.0,
        "min": float('inf'),
        "max": 0.0
    }

    try:
        for message in consumer:
            dados = message.value
            operacao = dados.get("operation", "UNKNOWN")
            duracao = dados.get("duration_seconds", 0.0)
            
            # Atualiza matemática
            stats_globais['count'] += 1
            stats_globais['sum'] += duracao
            if duracao < stats_globais['min']: stats_globais['min'] = duracao
            if duracao > stats_globais['max']: stats_globais['max'] = duracao
            
            renderizar_painel(operacao, duracao, stats_globais)
            
    except KeyboardInterrupt:
        print("\n\n🛑 Hub de telemetria encerrado de forma segura.")
    finally:
        consumer.close()

if __name__ == "__main__":
    main()