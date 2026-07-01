"""
BENCHMARK DE LATÊNCIA DE REDE (NETWORK_DELAY)
=============================================

Agora que NETWORK_DELAY afeta também o caminho de UPLOAD (o fan-out do ingress
para as réplicas, via net_sim.apply_network_delay), este teste mede o impacto do
atraso de rede no tempo de upload da CLI — e a variável de fato move o número.

Onde o atraso é aplicado (helper centralizado dfs/cluster/net_sim.py):
  - fan-out do upload (ingress -> réplicas)   <== é o que este teste exercita
  - failover do download (egress -> peer)
  - re-replicação (nó-fonte -> destino)

Como o fan-out roda em paralelo por réplica, cada chunk paga ~1x NETWORK_DELAY;
com vários chunks (arquivo maior), o efeito acumula e fica bem visível.

Como usar (compare cenários):
    # 1) rede ideal
    export NETWORK_DELAY=0
    python run_cluster.py          # numa janela
    python benchmark/test_network_delay.py   # noutra

    # 2) rede com atraso — pare o cluster, reexporte e suba de novo
    export NETWORK_DELAY=1.0
    python run_cluster.py
    python benchmark/test_network_delay.py

Monte um gráfico de barras dos tempos médios por valor de NETWORK_DELAY para o
relatório.
"""
import os
import sys
import time
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent  # Final/
CLI = str(ROOT_DIR / "run_cli.py")
ARQ_LOCAL = str(ROOT_DIR / "teste_rede.txt")
CAMINHO_LOGICO = "testes/rede.bin"
FILE_SIZE_MB = 8  # maior que antes: mais chunks => efeito do atraso mais visível

CLI_UPLOAD_CMD = [sys.executable, CLI, "put", ARQ_LOCAL, CAMINHO_LOGICO]


def medir_tempo_upload(nome_cenario: str) -> float:
    print(f"\n[Cenário: {nome_cenario}]")
    start = time.time()
    subprocess.run(CLI_UPLOAD_CMD, check=True, cwd=str(ROOT_DIR), stdout=subprocess.DEVNULL)
    dur = time.time() - start
    print(f"-> ⏱️ Tempo: {dur:.2f}s")
    return dur


def main():
    print(f"\n{'='*55}")
    print("📶 BENCHMARK DE LATÊNCIA DE REDE (NETWORK_DELAY no upload)")
    print(f"{'='*55}\n")

    atual = os.getenv("NETWORK_DELAY", "0.0")
    print(f"NETWORK_DELAY do AMBIENTE deste terminal: {atual}s")
    print("Lembre: o valor que importa é o que o CLUSTER (run_cluster.py) enxergou")
    print("ao subir; reexporte e reinicie o cluster para mudar o atraso real.\n")

    print(f"Gerando arquivo de teste ({FILE_SIZE_MB}MB)...")
    with open(ARQ_LOCAL, "wb") as f:
        f.write(os.urandom(FILE_SIZE_MB * 1024 * 1024))

    input("Pressione ENTER quando o cluster estiver rodando para começar...")

    tempos = [medir_tempo_upload(f"Upload {i}/3") for i in range(1, 4)]
    media = sum(tempos) / len(tempos)

    print(f"\n{'='*55}")
    print("📊 RESULTADOS:")
    print(f"   Tempos: {[round(t, 2) for t in tempos]} s")
    print(f"   Média : {media:.2f} s  (com NETWORK_DELAY≈{atual}s no cluster)")
    print(f"{'='*55}\n")
    print("Rode com NETWORK_DELAY=0, 1.0 e 3.0 (reiniciando o cluster a cada vez)")
    print("e compare as médias num gráfico de barras.")

    if os.path.exists(ARQ_LOCAL):
        os.remove(ARQ_LOCAL)


if __name__ == "__main__":
    main()
