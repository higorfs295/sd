"""
Benchmark de Heartbeat (justificativa empírica dos limiares SUSPECT e DEAD)

Mede a premissa que sustenta a escolha dos timeouts: sob carga pesada, o intervalo real entre batimentos AUMENTA (a thread de heartbeat fica sem CPU enquanto o no processa upload).
Esse atraso e a "cauda da distribuicao" que o timeout de morte precisa cobrir.

Dois modos:

  MODO 1 (padrão) - distribuição ocioso vs sob carga
    Mede o intervalo entre batimentos com o sistema ocioso e depois sob carga, e grava media, max, p95 e p99 de cada fase.
    Uso:
      python benchmark/benchmark_heartbeat.py --port 9100 --duracao 30 --carga-mb 50

  MODO 2 (--varredura) - compara varios limiares DEAD
    Reaproveita os atrasos medidos sob carga e, para cada DEAD candidato, conta quantos batimentos teriam estourado o limiar (falsos positivos de morte).
    Prova qual e o menor DEAD que ainda da zero falso positivo.
    Uso:
      python benchmark/benchmark_heartbeat.py --port 9100 --duracao 40 --carga-mb 50 --varredura 4 8 12 16 20 25

O cluster (coordenador + Kafka) precisa estar no ar nos dois modos.
"""

import os
import sys
import csv
import time
import random
import string
import argparse
import threading
import statistics
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

_DFS_DIR = Path(__file__).resolve().parent.parent / "DFS"
if str(_DFS_DIR) not in sys.path:
    sys.path.insert(0, str(_DFS_DIR))

from dfs.config import HEARTBEAT_INTERVAL, HEARTBEAT_SUSPECT, HEARTBEAT_DEAD
from dfs.client import DataClient as DFSClient

_CSV_DIR = Path(__file__).resolve().parent / "csv"


def parse_args():
    p = argparse.ArgumentParser(description="DFS - Benchmark de heartbeat")
    p.add_argument("--host", type=str, default="localhost")
    p.add_argument("--port", type=int, default=9100, help="Coordenador port")
    p.add_argument(
        "--duracao", type=int, default=30, help="Segundos de medicao em cada fase"
    )
    p.add_argument(
        "--carga-mb",
        type=int,
        default=50,
        help="Tamanho de cada upload de carga, em MB",
    )
    p.add_argument(
        "--carga-clientes",
        type=int,
        default=4,
        help="Quantos uploads simultaneos geram a carga",
    )
    p.add_argument(
        "--varredura",
        type=int,
        nargs="+",
        default=None,
        help="Lista de limiares DEAD a comparar (ativa o MODO 2)",
    )
    p.add_argument(
        "--output",
        type=str,
        default=None,
        help="Nome do CSV (em benchmark/csv/) ou caminho absoluto. "
        "Se omitido, usa resultados_heartbeat.csv (modo padrão) "
        "ou resultados_heartbeat_varredura.csv (--varredura), "
        "para nunca misturar os dois formatos no mesmo arquivo.",
    )
    return p.parse_args()


def medir_intervalos(duracao_s):
    """Envia batimentos no ritmo nominal e registra o intervalo REAL entre eles."""
    intervalos = []
    anterior = time.monotonic()
    fim = anterior + duracao_s
    while time.monotonic() < fim:
        time.sleep(HEARTBEAT_INTERVAL)
        agora = time.monotonic()
        intervalos.append(agora - anterior)
        anterior = agora
    return intervalos


def gerar_arquivo(filename, size_mb):
    bloco = "".join(random.choices(string.ascii_letters + string.digits, k=1024 * 1024))
    with open(filename, "w") as f:
        for _ in range(size_mb):
            f.write(bloco)


def _percentil(s, p):
    n = len(s)
    if n == 0:
        return 0.0
    k = (n - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, n - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def resumir(intervalos):
    s = sorted(intervalos)
    return {
        "amostras": len(s),
        "media_s": round(statistics.mean(s), 4) if s else 0.0,
        "max_s": round(max(s), 4) if s else 0.0,
        "p95_s": round(_percentil(s, 95), 4),
        "p99_s": round(_percentil(s, 99), 4),
    }


def rodar_carga(args, duracao_s):
    """
    Mede os intervalos entre batimentos enquanto a maquina esta sob carga.
    A carga sao uploads pesados em loop, em varias threads, saturando CPU/disco.
    Retorna a lista de intervalos medidos.
    """
    parar = threading.Event()
    local_src = f"hb_carga_{args.carga_mb}MB.dat"
    gerar_arquivo(local_src, args.carga_mb)
    client = DFSClient(args.host, args.port)

    def gerar_carga(worker_id):
        i = 0
        while not parar.is_set():
            try:
                client.upload_file(local_src, f"/bench_hb/w{worker_id}_{i}.dat")
                i += 1
            except Exception:
                pass

    try:
        with ThreadPoolExecutor(max_workers=args.carga_clientes) as ex:
            for w in range(args.carga_clientes):
                ex.submit(gerar_carga, w)
            intervalos = medir_intervalos(duracao_s)
            parar.set()
    finally:
        if os.path.exists(local_src):
            os.remove(local_src)
    return intervalos


def main():
    args = parse_args()

    # Escolhe o nome do CSV automaticamente conforme o modo, para os dois
    # formatos (6 colunas no modo padrão, 8 no --varredura) NUNCA caírem no
    # mesmo arquivo. Se o usuário passar --output, respeita a escolha dele.
    if args.output is None:
        args.output = (
            "resultados_heartbeat_varredura.csv"
            if args.varredura
            else "resultados_heartbeat.csv"
        )

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = _CSV_DIR / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(
        f"Config atual: INTERVAL={HEARTBEAT_INTERVAL}s "
        f"SUSPECT={HEARTBEAT_SUSPECT}s DEAD={HEARTBEAT_DEAD}s"
    )

    # Fase ocioso (comum aos dois modos).
    print(f"\n[1/2] Medindo OCIOSO por {args.duracao}s...")
    ociosos = medir_intervalos(args.duracao)
    r_ocioso = resumir(ociosos)
    print(
        f"  -> media={r_ocioso['media_s']}s  max={r_ocioso['max_s']}s  "
        f"p95={r_ocioso['p95_s']}s  p99={r_ocioso['p99_s']}s"
    )

    # Fase sob carga (comum aos dois modos).
    print(
        f"\n[2/2] Medindo SOB CARGA por {args.duracao}s "
        f"({args.carga_clientes} uploads de {args.carga_mb}MB em loop)..."
    )
    sob_carga = rodar_carga(args, args.duracao)
    r_carga = resumir(sob_carga)
    print(
        f"  -> media={r_carga['media_s']}s  max={r_carga['max_s']}s  "
        f"p95={r_carga['p95_s']}s  p99={r_carga['p99_s']}s"
    )

    if args.varredura:
        # MODO 2: para cada DEAD candidato, conta os batimentos que estourariam
        # o limiar sob carga (falsos positivos de morte).
        campos = [
            "fase",
            "dead_testado",
            "amostras",
            "media_s",
            "max_s",
            "p95_s",
            "p99_s",
            "falsos_positivos",
        ]
        novo = not out_path.exists()
        with open(out_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=campos)
            if novo:
                w.writeheader()
            print("\n" + "=" * 60)
            print("VARREDURA DE LIMIAR DEAD (sob carga):")
            for dead in sorted(args.varredura):
                fp = sum(1 for x in sob_carga if x >= dead)
                linha = {
                    "fase": "sob_carga",
                    "dead_testado": dead,
                    "falsos_positivos": fp,
                }
                linha.update(r_carga)
                w.writerow(linha)
                print(f"  DEAD={dead:>3}s -> {fp} falso(s) positivo(s)")
            print("=" * 60)
            menor_ok = min(
                (
                    d
                    for d in sorted(args.varredura)
                    if sum(1 for x in sob_carga if x >= d) == 0
                ),
                default=None,
            )
            if menor_ok is not None:
                print(f"\nMenor DEAD com ZERO falso positivo: {menor_ok}s")
                print("Esse e o valor otimo: o mais agil que ainda nao erra.")
            else:
                print(
                    "\nNenhum DEAD testado zerou os falsos positivos. "
                    "Aumente os valores da varredura."
                )
    else:
        # MODO 1: grava a distribuicao das duas fases.
        campos = ["fase", "amostras", "media_s", "max_s", "p95_s", "p99_s"]
        novo = not out_path.exists()
        with open(out_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=campos)
            if novo:
                w.writeheader()
            w.writerow({"fase": "ocioso", **r_ocioso})
            w.writerow({"fase": "sob_carga", **r_carga})

        print("\n" + "=" * 60)
        print(f"  Pior atraso sob carga (max): {r_carga['max_s']}s")
        print(f"  Limiar DEAD configurado:     {HEARTBEAT_DEAD}s")
        if r_carga["max_s"] < HEARTBEAT_DEAD:
            margem = round(HEARTBEAT_DEAD / max(r_carga["max_s"], 0.001), 1)
            print(f"  -> DEAD cobre o pior atraso com folga de ~{margem}x.")
        else:
            print(f"  -> ATENCAO: o pior atraso passou do DEAD. Aumente o limiar.")
        print("=" * 60)

    print(f"\nCSV salvo em: {out_path}")


if __name__ == "__main__":
    main()
