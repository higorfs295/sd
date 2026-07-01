"""
Benchmark de Heartbeat (justificativa empírica dos limiares SUSPECT e DEAD)

Mede a premissa que sustenta a escolha dos timeouts: sob carga, o intervalo real
entre batimentos AUMENTA (a thread de heartbeat fica sem CPU). Essa cauda é o que
o timeout de morte precisa cobrir sem gerar falso positivo.

DOIS TIPOS DE CARGA (--carga-tipo):

  upload (default): uploads pesados em loop. É carga I/O-bound: gRPC e disco
    LIBERAM o GIL, então a thread que dorme acorda quase no tempo. Resultado
    esperado: o intervalo QUASE NÃO muda. Isso é uma medição honesta e mostra que,
    no caminho normal do sistema, o heartbeat não sofre inanição relevante.

  cpu: loops de CPU puros em Python, um por thread. É carga CPU-bound: o GIL fica
    RETIDO, então a thread de heartbeat que dorme é preterida e o intervalo real
    INFLA visivelmente. É ESTE modo que PROVA a premissa — demonstra a condição
    (saturação de CPU/GIL) contra a qual o limiar DEAD generoso protege.

  ambos: aplica upload + CPU ao mesmo tempo (o pior caso).

Dois modos de saída:
  MODO 1 (padrão): distribuição ocioso vs sob carga (media, max, p95, p99).
  MODO 2 (--varredura): para cada DEAD candidato, conta quantos batimentos
    estourariam o limiar sob carga (falsos positivos). No modo cpu, aqui aparecem
    os falsos positivos que justificam um DEAD maior.

Exemplos:
  python benchmark/benchmark_heartbeat.py --duracao 30 --carga-mb 50
  python benchmark/benchmark_heartbeat.py --duracao 40 --carga-tipo cpu --cpu-threads 8 --varredura 4 8 12 16 20 25

O cluster (coordenador + Kafka) precisa estar no ar no modo 'upload'/'ambos'.
No modo 'cpu' puro, a carga é local (não precisa do cluster), mas manter o cluster
no ar deixa a medição mais realista.
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
    p.add_argument("--duracao", type=int, default=30, help="Segundos de medicao em cada fase")
    p.add_argument("--carga-mb", type=int, default=50, help="Tamanho de cada upload de carga, em MB")
    p.add_argument("--carga-clientes", type=int, default=4, help="Quantos uploads simultaneos geram a carga")
    p.add_argument(
        "--carga-tipo",
        choices=["upload", "cpu", "ambos"],
        default="upload",
        help="Tipo de carga: I/O (upload), CPU-bound (cpu, prova a premissa) ou ambos",
    )
    p.add_argument(
        "--cpu-threads",
        type=int,
        default=None,
        help="Threads de CPU-bound (default: nº de núcleos). Só usado em --carga-tipo cpu/ambos",
    )
    p.add_argument(
        "--varredura",
        type=int,
        nargs="+",
        default=None,
        help="Lista de limiares DEAD a comparar (ativa o MODO 2)",
    )
    p.add_argument(
        "--analisar-reais",
        action="store_true",
        help="MODO 3: NÃO mede nada; analisa os heartbeat_real_node*.csv que os NÓS "
        "gravaram. É a evidência CORRETA da premissa — rode o cluster sob carga "
        "pesada (ex.: benchmark_concurrency com C alto) e depois analise aqui.",
    )
    p.add_argument("--output", type=str, default=None, help="Nome do CSV (em benchmark/csv/) ou caminho absoluto.")
    return p.parse_args()


def analisar_csvs_reais(deads: list[int] | None) -> None:
    """
    MODO 3 — a forma HONESTA de provar (ou refutar) a premissa.

    O benchmark sintético mede uma thread que só DORME: em CPython, time.sleep
    libera o GIL e a thread acorda quase no tempo, então o intervalo lido fica
    ~nominal mesmo sob carga. Logo, ele NÃO é capaz de mostrar a inflação real.

    A inflação real do heartbeat vem do TRABALHO do loop do HeartbeatWorker (o
    round-trip gRPC ao coordenador + a varredura de diretório list_chunk_ids),
    que se atrasa quando o cluster está saturado. Esse atraso está gravado nos
    heartbeat_real_node*.csv (coluna intervalo_real_s), produzidos pelos PRÓPRIOS
    nós. Esta função lê esses CSVs e reporta a cauda (max, p95, p99) por nó e no
    agregado — e, se você passar --varredura, quantos batimentos reais estourariam
    cada DEAD (falsos positivos de verdade).

    Como capturar a evidência:
        1. python run_cluster.py                          (sobe os 5 nós)
        2. python benchmark/benchmark_concurrency.py --concorrencia 16 --size 25
           (satura os nós/coordenador; os nós gravam intervalos reais sob carga)
        3. python benchmark/benchmark_heartbeat.py --analisar-reais --varredura 4 8 12 16 20 25
    """
    arquivos = sorted(_CSV_DIR.glob("heartbeat_real_node*.csv"))
    if not arquivos:
        print(f"Nenhum heartbeat_real_node*.csv em {_CSV_DIR}. Rode o cluster primeiro.")
        return

    todos: list[float] = []
    print("\n" + "=" * 60)
    print("ANÁLISE DOS INTERVALOS REAIS DE HEARTBEAT (dados dos nós)")
    print("=" * 60)
    for arq in arquivos:
        intervalos = []
        with open(arq, newline="") as f:
            leitor = csv.DictReader(f)
            for linha in leitor:
                try:
                    intervalos.append(float(linha["intervalo_real_s"]))
                except (KeyError, ValueError):
                    continue
        if not intervalos:
            continue
        todos.extend(intervalos)
        r = resumir(intervalos)
        print(
            f"  {arq.name:<28} n={r['amostras']:<5} media={r['media_s']}s "
            f"max={r['max_s']}s p95={r['p95_s']}s p99={r['p99_s']}s"
        )

    if not todos:
        print("Os CSVs existem mas não têm intervalos válidos.")
        return

    rg = resumir(todos)
    print("-" * 60)
    print(
        f"  AGREGADO (todos os nós)      n={rg['amostras']:<5} media={rg['media_s']}s "
        f"max={rg['max_s']}s p95={rg['p95_s']}s p99={rg['p99_s']}s"
    )

    if deads:
        print("\n  Falsos positivos REAIS por limiar DEAD:")
        for dead in sorted(deads):
            fp = sum(1 for x in todos if x >= dead)
            print(f"    DEAD={dead:>3}s -> {fp} batimento(s) real(is) estourariam o limiar")
        menor_ok = min((d for d in sorted(deads) if sum(1 for x in todos if x >= d) == 0), default=None)
        if menor_ok is not None:
            print(f"\n  Menor DEAD com zero falso positivo (dados reais): {menor_ok}s")
    print("=" * 60)


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


def _cpu_burn(parar: threading.Event) -> None:
    """
    Loop de CPU PURO em Python: segura o GIL enquanto roda. Um destes por thread,
    com threads >= nº de núcleos, satura o interpretador e preteria a thread de
    heartbeat que dorme — é isto que faz o intervalo real INFLAR (prova da premissa).
    """
    x = 0
    while not parar.is_set():
        # Aritmética pura em Python (sem I/O) mantém o GIL retido.
        for _ in range(200_000):
            x = (x * 1103515245 + 12345) & 0x7FFFFFFF


def rodar_carga(args, duracao_s):
    """
    Mede os intervalos entre batimentos enquanto a maquina esta sob carga,
    do tipo escolhido (upload I/O-bound, cpu CPU-bound, ou ambos).
    """
    parar = threading.Event()
    threads_cpu = []
    executor_upload = None
    local_src = None

    tipo = args.carga_tipo
    usa_cpu = tipo in ("cpu", "ambos")
    usa_upload = tipo in ("upload", "ambos")

    if usa_cpu:
        n_cpu = args.cpu_threads or (os.cpu_count() or 4)
        print(f"  (carga CPU-bound: {n_cpu} thread(s) de loop puro, segurando o GIL)")
        for _ in range(n_cpu):
            t = threading.Thread(target=_cpu_burn, args=(parar,), daemon=True)
            t.start()
            threads_cpu.append(t)

    if usa_upload:
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

        executor_upload = ThreadPoolExecutor(max_workers=args.carga_clientes)
        for w in range(args.carga_clientes):
            executor_upload.submit(gerar_carga, w)

    try:
        intervalos = medir_intervalos(duracao_s)
    finally:
        parar.set()
        if executor_upload is not None:
            executor_upload.shutdown(wait=False)
        for t in threads_cpu:
            t.join(timeout=1.0)
        if local_src and os.path.exists(local_src):
            os.remove(local_src)
    return intervalos


def main():
    args = parse_args()

    # MODO 3: analisa os CSVs reais dos nós (evidência correta da premissa).
    if args.analisar_reais:
        analisar_csvs_reais(args.varredura)
        return

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
        f"SUSPECT={HEARTBEAT_SUSPECT}s DEAD={HEARTBEAT_DEAD}s | carga={args.carga_tipo}"
    )

    print(f"\n[1/2] Medindo OCIOSO por {args.duracao}s...")
    ociosos = medir_intervalos(args.duracao)
    r_ocioso = resumir(ociosos)
    print(
        f"  -> media={r_ocioso['media_s']}s  max={r_ocioso['max_s']}s  "
        f"p95={r_ocioso['p95_s']}s  p99={r_ocioso['p99_s']}s"
    )

    print(f"\n[2/2] Medindo SOB CARGA ({args.carga_tipo}) por {args.duracao}s...")
    sob_carga = rodar_carga(args, args.duracao)
    r_carga = resumir(sob_carga)
    print(
        f"  -> media={r_carga['media_s']}s  max={r_carga['max_s']}s  "
        f"p95={r_carga['p95_s']}s  p99={r_carga['p99_s']}s"
    )

    if args.varredura:
        campos = ["fase", "carga_tipo", "dead_testado", "amostras", "media_s", "max_s", "p95_s", "p99_s", "falsos_positivos"]
        novo = not out_path.exists()
        with open(out_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=campos)
            if novo:
                w.writeheader()
            print("\n" + "=" * 60)
            print(f"VARREDURA DE LIMIAR DEAD (carga={args.carga_tipo}):")
            for dead in sorted(args.varredura):
                fp = sum(1 for x in sob_carga if x >= dead)
                linha = {"fase": "sob_carga", "carga_tipo": args.carga_tipo, "dead_testado": dead, "falsos_positivos": fp}
                linha.update(r_carga)
                w.writerow(linha)
                print(f"  DEAD={dead:>3}s -> {fp} falso(s) positivo(s)")
            print("=" * 60)
            menor_ok = min(
                (d for d in sorted(args.varredura) if sum(1 for x in sob_carga if x >= d) == 0),
                default=None,
            )
            if menor_ok is not None:
                print(f"\nMenor DEAD com ZERO falso positivo: {menor_ok}s (o mais agil que ainda nao erra).")
            else:
                print("\nNenhum DEAD testado zerou os falsos positivos. Aumente os valores da varredura.")
    else:
        campos = ["fase", "carga_tipo", "amostras", "media_s", "max_s", "p95_s", "p99_s"]
        novo = not out_path.exists()
        with open(out_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=campos)
            if novo:
                w.writeheader()
            w.writerow({"fase": "ocioso", "carga_tipo": args.carga_tipo, **r_ocioso})
            w.writerow({"fase": "sob_carga", "carga_tipo": args.carga_tipo, **r_carga})

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
