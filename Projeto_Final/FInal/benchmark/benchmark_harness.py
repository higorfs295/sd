"""
Benchmark de Volume (variação do tamanho de arquivo, em série)
Complementa o benchmark_concurrency.py (que mede a taxa de requisições, em paralelo) medindo o desempenho bruto do sistema com UM cliente por vez, à medida que o tamanho do arquivo cresce.

Para cada tamanho de arquivo (ex.: 1, 5, 10, 25, 50 MB) e cada iteração:
  1. faz UM upload do arquivo para um caminho lógico, em série, e mede:
       - tempo_segundos   : tempo de parede do upload;
       - throughput_mbs   : tamanho / tempo   [MB/s de um único cliente].
  2. faz UM download do mesmo arquivo, em série, e mede tempo e throughput.
  Várias iterações por tamanho permitem observar a média e a variância.

Por que rodar em série (um de cada vez):
  O objetivo aqui é medir o desempenho BASE do sistema, sem contenção entre requisições concorrentes.
  Isola o efeito do tamanho do arquivo no tempo e na vazão.
  A medição sob carga concorrente (vários clientes ao mesmo tempo) é responsabilidade do benchmark_concurrency.py.

O que esperar dos resultados:
  o download tende a ser mais rápido que o upload, porque o upload paga a replicação síncrona (grava várias cópias e espera o quórum), enquanto o download lê de uma única réplica.
  a variância tende a crescer com o tamanho do arquivo, por causa da contenção de recursos no ambiente de máquina única (vários processos disputam CPU e disco).

- Grava em benchmark/csv/resultados_volume.csv
Uso:
  python benchmark/benchmark_volume.py
"""

import os
import sys
import time
import csv
import random
import string
import logging
import argparse
from pathlib import Path

_DFS_DIR = Path(__file__).resolve().parent.parent / "DFS"
if str(_DFS_DIR) not in sys.path:
    sys.path.insert(0, str(_DFS_DIR))

from dfs.client import DataClient as DFSClient

_CSV_DIR = Path(__file__).resolve().parent / "csv"

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def parse_arguments():
    parser = argparse.ArgumentParser(description="DFS Benchmark Harness - Marco 5")
    parser.add_argument(
        "--host", type=str, default="localhost", help="Coordenador Host"
    )
    parser.add_argument("--port", type=int, default=9100, help="Coordenador Port")
    parser.add_argument(
        "--output",
        type=str,
        default="resultados_volume.csv",
        help="Nome do CSV (gravado em benchmark/csv/) ou um caminho absoluto",
    )
    parser.add_argument(
        "--nodes", type=int, default=5, help="Número de nós ativos no teste atual"
    )
    parser.add_argument(
        "--iter", type=int, default=3, help="Número de iterações por tamanho"
    )
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=[1, 5, 10, 25, 50],
        help="Tamanhos em MB (ex: --sizes 1 10 50)",
    )
    return parser.parse_args()


def generate_dummy_file(filename, size_mb):
    """Gera arquivo com dados pseudo-aleatórios usando chunks grandes para otimizar a criação."""
    logger.info(f"Gerando arquivo {filename} de {size_mb}MB...")
    chunk_size = 1024 * 1024  # 1MB chunks
    chars = "".join(random.choices(string.ascii_letters + string.digits, k=chunk_size))
    with open(filename, "w") as f:
        for _ in range(size_mb):
            f.write(chars)


def run_benchmark():
    args = parse_arguments()

    # Destino do CSV: se --output for absoluto, respeita; senão, grava em# Final/benchmark/csv/<nome>. A pasta é criada se não existir.
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = _CSV_DIR / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # IMPORTANTE: O benchmark deve apontar para o COORDENADOR (porta padrão do config ou repassada por argumento) se o DataClient de alto nível usa o ControlClient internamente.
    client = DFSClient(args.host, args.port)

    # Escrever cabeçalho do CSV se não existir
    if not out_path.exists():
        with open(out_path, mode="w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    "operacao",
                    "tamanho_mb",
                    "tempo_segundos",
                    "throughput_mbs",
                    "nos_ativos",
                ]
            )

    for size in args.sizes:
        local_filename = f"bench_local_{size}MB.dat"
        remote_filename = f"/bench_remoto_{size}MB.dat"
        download_filename = f"bench_baixado_{size}MB.dat"

        generate_dummy_file(local_filename, size)

        for i in range(args.iter):
            logger.info(
                f"Iniciando Iteração {i+1}/{args.iter} para {size}MB com {args.nodes} nós ativos."
            )

            try:
                # --- Teste de UPLOAD (Usando o método correto de alto nível) ---
                start_time = time.time()
                client.upload_file(local_filename, remote_filename)
                upload_time = time.time() - start_time
                throughput_up = size / upload_time

                # --- Teste de DOWNLOAD (Usando o método correto de alto nível) ---
                start_time = time.time()
                client.download_file(remote_filename, download_filename)
                download_time = time.time() - start_time
                throughput_down = size / download_time

                # Salvar os resultados no CSV
                with open(out_path, mode="a", newline="") as file:
                    writer = csv.writer(file)
                    writer.writerow(
                        [
                            "upload",
                            size,
                            round(upload_time, 4),
                            round(throughput_up, 4),
                            args.nodes,
                        ]
                    )
                    writer.writerow(
                        [
                            "download",
                            size,
                            round(download_time, 4),
                            round(throughput_down, 4),
                            args.nodes,
                        ]
                    )

                logger.info(
                    f"Sucesso! Up: {throughput_up:.2f} MB/s | Down: {throughput_down:.2f} MB/s"
                )

            except Exception as e:
                logger.error(f"Falha na iteração {i+1} de {size}MB: {str(e)}")

        # Limpeza de arquivos locais gerados pelo benchmark nesta iteração
        if os.path.exists(local_filename):
            os.remove(local_filename)
        if os.path.exists(download_filename):
            os.remove(download_filename)

    logger.info(f"Benchmark finalizado. Arquivo salvo em: {out_path}")


if __name__ == "__main__":
    run_benchmark()
