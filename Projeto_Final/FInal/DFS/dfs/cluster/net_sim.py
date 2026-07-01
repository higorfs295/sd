# dfs/cluster/net_sim.py
"""
Simulação de latência de rede, centralizada num único ponto.

Antes, o atraso era lido só em kafka_listener._transfer_chunk (re-replicação),
então a variável NETWORK_DELAY não afetava o caminho de UPLOAD que o
test_network_delay.py mede. Agora todos os pontos que transferem bytes pela rede
(fan-out do upload, failover do download e re-replicação) chamam a MESMA função
daqui, então uma única variável de ambiente simula latência de rede de forma
uniforme no cluster inteiro.

Uso:
    export NETWORK_DELAY=1.5   # segundos por transferência de chunk pela rede
    (0 ou ausente = sem atraso, comportamento normal)
"""
from __future__ import annotations

import os
import time


def network_delay_seconds() -> float:
    """Lê NETWORK_DELAY de forma tolerante. Valor inválido/ausente => 0.0."""
    try:
        return max(0.0, float(os.getenv("NETWORK_DELAY", "0.0")))
    except (TypeError, ValueError):
        return 0.0


def apply_network_delay(context: str = "", node_id: str = "") -> float:
    """
    Dorme NETWORK_DELAY segundos, se configurado, simulando o custo de uma
    transferência de chunk pela rede. Retorna o atraso aplicado (0.0 se nenhum).

    'context' e 'node_id' são só para o log opcional (fan-out, fetch, rereplicação).
    """
    atraso = network_delay_seconds()
    if atraso > 0:
        prefixo = f"[{node_id}] " if node_id else ""
        rotulo = f" ({context})" if context else ""
        print(f"{prefixo}[NETWORK_DELAY]{rotulo} simulando {atraso:.2f}s de rede...")
        time.sleep(atraso)
    return atraso
