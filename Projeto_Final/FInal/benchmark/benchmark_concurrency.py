"""
DFS - Benchmark de CONCORRÊNCIA (variação da taxa de requisições)
=================================================================
Complementa o benchmark_harness.py (que mede VOLUME, em série) medindo o eixo que
faltava da spec: "variação da taxa de requisições" — ou seja, vários CLIENTES
disparando uploads/downloads AO MESMO TEMPO.

Para cada nível de concorrência C (ex.: 1, 2, 4, 8, 16) e cada tamanho de arquivo:
  1. dispara C uploads simultâneos (um ThreadPoolExecutor, C workers), cada um para
     um caminho lógico distinto, e mede:
       - wall_time           : tempo de parede até TODOS terminarem;
       - throughput_agregado : (C * tamanho) / wall_time   [MB/s do sistema todo];
       - latencia_media/p95  : distribuição do tempo POR requisição;
       - req_por_s           : C / wall_time;
       - erros               : requisições que falharam.
  2. baixa de volta os arquivos que subiram, também em C downloads simultâneos.

Por que cada requisição cria o próprio cliente:
  client.upload_file()/download_file() já são autocontidos — cada chamada abre o
  próprio ControlClient + DataClient(ingress/egress) e fecha no fim. Logo, rodar
  N em paralelo simula N clientes independentes de verdade (não há estado
  compartilhado entre as chamadas).

IMPORTANTE (honestidade):
  - Pode rodar de QUALQUER pasta: o script insere Final/DFS no sys.path sozinho
    (mesma técnica do run_cli.py), então 'import dfs...' resolve sempre.
  - O endereço do coordenador vem do config.py (não do --host/--port; esses métodos
    de alto nível ignoram esses argumentos — mantidos só por paridade).
  - Grava em benchmark/csv/resultados_concorrencia.csv (arquivo PRÓPRIO; NÃO alimenta o gráfico de
    elasticidade do plot_metrics, que é o de variação de Nº DE NÓS).
  - Eu não rodei isto contra um cluster real (não tenho Kafka/cluster aqui); validei
    só a sintaxe. A prova é você rodar.

Uso:
  python benchmark/benchmark_concurrency.py
  python benchmark/benchmark_concurrency.py --size 5 --concorrencia 1 2 4 8 16 --nodes 5
"""

import os
import csv
import time
import uuid
import random
import string
import logging
import sys
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Bootstrap de path (mesma técnica do run_cli.py) -------------------------
# Este script vive em Final/benchmark/, mas o pacote 'dfs' está em Final/DFS/.
# Inserimos Final/DFS no sys.path para que 'import dfs...' resolva INDEPENDENTE
# de onde você roda (da raiz Final/, de dentro de benchmark/, por caminho
# absoluto, etc.). É a raiz do bug 'No module named DFS' da versão anterior:
# o Python põe no path a pasta do SCRIPT (benchmark/), não a raiz do projeto.
_DFS_DIR = Path(__file__).resolve().parent.parent / "DFS"
if str(_DFS_DIR) not in sys.path:
    sys.path.insert(0, str(_DFS_DIR))
# -----------------------------------------------------------------------------

from dfs.client import DataClient as DFSClient

# Telemetria opcional (mesmo tópico do telemetry_hub). Import protegido: se faltar
# o publisher ou o kafka-python, o benchmark roda igual, só sem alimentar o hub.
try:
    from dfs.cluster.kafka_publisher import ClusterEventPublisher
except Exception:
    ClusterEventPublisher = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# Pasta padrao de saida dos CSVs: Final/benchmark/csv/ (ancorada no SCRIPT, nao
# no cwd) -> o CSV nunca cai na raiz do projeto, rode voce de onde rodar.
_CSV_DIR = Path(__file__).resolve().parent / "csv"


def parse_arguments():
    p = argparse.ArgumentParser(description="DFS - Benchmark de concorrência (taxa de requisições)")
    p.add_argument("--host", type=str, default="localhost", help="(ignorado pelos métodos de alto nível; vem do config)")
    p.add_argument("--port", type=int, default=50051, help="(ignorado pelos métodos de alto nível; vem do config)")
    p.add_argument("--size", type=int, default=5, help="Tamanho de cada arquivo, em MB (fixo, para isolar o efeito da taxa)")
    p.add_argument("--concorrencia", type=int, nargs="+", default=[1, 2, 4, 8, 16],
                   help="Níveis de concorrência a testar (clientes simultâneos)")
    p.add_argument("--nodes", type=int, default=5, help="Nº de nós ativos (apenas rótulo no CSV)")
    p.add_argument("--output", type=str, default="resultados_concorrencia.csv",
                   help="Nome do CSV (gravado em benchmark/csv/) ou um caminho absoluto")
    p.add_argument("--no-telemetria", action="store_true", help="Não publica métricas no Kafka")
    return p.parse_args()


def gerar_arquivo(filename: str, size_mb: int) -> None:
    """Gera um arquivo de size_mb usando blocos de 1MB (rápido)."""
    bloco = "".join(random.choices(string.ascii_letters + string.digits, k=1024 * 1024))
    with open(filename, "w") as f:
        for _ in range(size_mb):
            f.write(bloco)


def _percentil(valores, p: float) -> float:
    """Percentil p (0-100) por interpolação linear, sem numpy."""
    if not valores:
        return 0.0
    s = sorted(valores)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _rodar_fase(tarefa, n_workers: int):
    """
    Dispara n_workers chamadas concorrentes de `tarefa(i)`.
    `tarefa(i)` deve devolver (duracao_segundos, info) em caso de sucesso, ou
    levantar exceção em caso de falha.

    Retorna: (wall_time, lista_de_duracoes, lista_de_infos_ok, n_erros)
    """
    duracoes, oks, erros = [], [], 0
    wall0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futuros = [ex.submit(tarefa, i) for i in range(n_workers)]
        for fut in as_completed(futuros):
            try:
                dur, info = fut.result()
                duracoes.append(dur)
                oks.append(info)
            except Exception as e:  # noqa: BLE001
                erros += 1
                logger.error(f"requisição falhou: {e}")
    wall = time.perf_counter() - wall0
    return wall, duracoes, oks, erros


def _linha_metricas(operacao, C, size_mb, wall, duracoes, n_ok, erros, nodes):
    """Monta a linha de métricas agregadas de uma fase (upload ou download)."""
    mb_transferidos = n_ok * size_mb
    throughput_agg = (mb_transferidos / wall) if wall > 0 else 0.0
    lat_media = (sum(duracoes) / len(duracoes)) if duracoes else 0.0
    lat_p95 = _percentil(duracoes, 95)
    req_s = (n_ok / wall) if wall > 0 else 0.0
    return {
        "operacao": operacao,
        "concorrencia": C,
        "tamanho_mb": size_mb,
        "wall_time_s": round(wall, 4),
        "throughput_agregado_mbs": round(throughput_agg, 4),
        "latencia_media_s": round(lat_media, 4),
        "latencia_p95_s": round(lat_p95, 4),
        "req_por_s": round(req_s, 4),
        "nos_ativos": nodes,
        "erros": erros,
    }


def run():
    args = parse_arguments()
    run_id = uuid.uuid4().hex[:8]  # evita colisão de caminho lógico entre execuções

    # Destino do CSV: se --output for absoluto, respeita; senão, grava em
    # Final/benchmark/csv/<nome>. A pasta é criada se não existir.
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = _CSV_DIR / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Cliente compartilhado: os métodos de alto nível são autocontidos (cada chamada
    # abre/fecha suas próprias conexões), então é seguro chamá-los de várias threads.
    client = DFSClient(args.host, args.port)

    # Telemetria (opcional). Publicamos a partir da thread principal, DEPOIS de cada
    # fase, para não depender de o publisher ser thread-safe.
    metrics = None
    if not args.no_telemetria and ClusterEventPublisher is not None:
        try:
            metrics = ClusterEventPublisher()
            logger.info("Telemetria ligada (tópico 'cluster-metrics').")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Telemetria desativada: {e}")
            metrics = None

    # Cabeçalho do CSV (só se o arquivo ainda não existir; senão faz append).
    campos = ["operacao", "concorrencia", "tamanho_mb", "wall_time_s",
              "throughput_agregado_mbs", "latencia_media_s", "latencia_p95_s",
              "req_por_s", "nos_ativos", "erros"]
    if not out_path.exists():
        with open(out_path, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=campos).writeheader()

    size = args.size
    local_src = f"conc_src_{size}MB_{run_id}.dat"
    gerar_arquivo(local_src, size)
    baixados = []  # arquivos locais de download, para limpar no fim

    try:
        for C in args.concorrencia:
            logger.info(f"=== Concorrência C={C} | tamanho={size}MB | nós(rótulo)={args.nodes} ===")

            # Caminhos lógicos distintos para esta rodada (um por cliente concorrente).
            remotos = [f"/conc/{run_id}/c{C}/u{i}_{size}MB.dat" for i in range(C)]

            # ---------- FASE UPLOAD ----------
            def tarefa_upload(i):
                t0 = time.perf_counter()
                client.upload_file(local_src, remotos[i])
                return time.perf_counter() - t0, remotos[i]

            wall, durs, oks, erros = _rodar_fase(tarefa_upload, C)
            linha = _linha_metricas("upload", C, size, wall, durs, len(oks), erros, args.nodes)
            with open(out_path, "a", newline="") as f:
                csv.DictWriter(f, fieldnames=campos).writerow(linha)
            logger.info(f"UPLOAD   C={C}: {linha['throughput_agregado_mbs']} MB/s agg | "
                        f"lat_media {linha['latencia_media_s']}s | p95 {linha['latencia_p95_s']}s | "
                        f"{linha['req_por_s']} req/s | erros={erros}")
            if metrics:
                for d in durs:
                    metrics.publish_metric("upload", d, extra={"concorrencia": C, "tamanho_mb": size})

            # Só baixa o que subiu de fato.
            remotos_ok = oks

            # ---------- FASE DOWNLOAD ----------
            def tarefa_download(i):
                remoto = remotos_ok[i]
                local_out = f"conc_dl_{run_id}_c{C}_{i}.dat"
                t0 = time.perf_counter()
                client.download_file(remoto, local_out)
                return time.perf_counter() - t0, local_out

            if remotos_ok:
                wall, durs, oks_dl, erros = _rodar_fase(tarefa_download, len(remotos_ok))
                baixados.extend(oks_dl)
                linha = _linha_metricas("download", C, size, wall, durs, len(oks_dl), erros, args.nodes)
                with open(out_path, "a", newline="") as f:
                    csv.DictWriter(f, fieldnames=campos).writerow(linha)
                logger.info(f"DOWNLOAD C={C}: {linha['throughput_agregado_mbs']} MB/s agg | "
                            f"lat_media {linha['latencia_media_s']}s | p95 {linha['latencia_p95_s']}s | "
                            f"{linha['req_por_s']} req/s | erros={erros}")
                if metrics:
                    for d in durs:
                        metrics.publish_metric("download", d, extra={"concorrencia": C, "tamanho_mb": size})
    finally:
        # Limpeza dos artefatos locais.
        if os.path.exists(local_src):
            os.remove(local_src)
        for f in baixados:
            if os.path.exists(f):
                os.remove(f)
        if metrics:
            metrics.close()

    logger.info(f"Concorrência finalizada. CSV: {out_path}")


if __name__ == "__main__":
    run()