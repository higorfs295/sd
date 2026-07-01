"""
TESTE DE CAOS (tolerância a falhas) — versão coerente com o modelo REAL do projeto.
====================================================================================

Contexto: neste projeto os NÓS rodam como PROCESSOS Python locais (via
run_cluster.py), não como contêineres Docker. O docker-compose.yml sobe apenas
Kafka/Zookeeper. Por isso este teste NÃO mata um contêiner: ele injeta a falha
matando o PROCESSO do nó-alvo (SIGKILL / TerminateProcess), espera o coordenador
detectar a morte, prova o failover de leitura (download íntegro com o nó morto),
e no fim RECUPERA o ambiente reiniciando o processo do nó.

Multiplataforma: usa a biblioteca `psutil` para achar e matar o processo pelo
command line ('--node-id nodeX'), funcionando igual no Windows e no Linux.

Pré-requisito: cluster no ar (python run_cluster.py) e `pip install psutil`.

Uso:
    python benchmark/test_chaos_advanced.py            # alvo padrão: node2
    python benchmark/test_chaos_advanced.py --node node3
"""
from __future__ import annotations

import os
import sys
import time
import argparse
import hashlib
import subprocess
from pathlib import Path

try:
    import psutil
except ImportError:
    print("❌ Falta a dependência 'psutil'. Rode: pip install psutil")
    sys.exit(1)

# Raiz do projeto (Final/) e pasta do pacote (Final/DFS/).
_HERE = Path(__file__).resolve()
ROOT_DIR = _HERE.parent.parent            # Final/
DFS_DIR = ROOT_DIR / "DFS"
if str(DFS_DIR) not in sys.path:
    sys.path.insert(0, str(DFS_DIR))

from dfs.client import DataClient as DFSClient  # noqa: E402

COORD_HOST = "127.0.0.1"
COORD_PORT = 9100
TEST_FILE = str(ROOT_DIR / "dados_vitais_chaos.dat")
REMOTE_PATH = "/chaos_test/dados_vitais.dat"


def get_hash(filepath: str) -> str:
    hasher = hashlib.md5()
    with open(filepath, "rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            hasher.update(bloco)
    return hasher.hexdigest()


def encontrar_processo_do_no(node_id: str) -> psutil.Process | None:
    """
    Procura o processo Python cujo command line contém 'storage_node' e
    '--node-id <node_id>'. Multiplataforma (não depende de PowerShell/wmic).
    """
    alvo = f"--node-id {node_id}"
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(proc.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if "storage_node" in cmd and alvo in cmd:
            return proc
    return None


def reiniciar_no(node_id: str) -> None:
    """Sobe de novo o processo do nó, com o mesmo PYTHONPATH que o run_cluster usa."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(DFS_DIR)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    subprocess.Popen(
        [sys.executable, "-m", "dfs.interface.storage_node", "--node-id", node_id],
        cwd=str(DFS_DIR),
        env=env,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Teste de caos por morte de processo de nó.")
    parser.add_argument("--node", default="node2", help="Nó-alvo a ser derrubado (default: node2)")
    parser.add_argument("--espera", type=int, default=25, help="Segundos aguardando a detecção da morte")
    args = parser.parse_args()
    node_id = args.node

    print("\n=======================================================")
    print("🔥 TESTE DE CAOS (morte de PROCESSO de nó) 🔥")
    print("=======================================================\n")

    # 1. Preparação
    print("-> Gerando arquivo de teste de 5MB...")
    with open(TEST_FILE, "wb") as f:
        f.write(os.urandom(5 * 1024 * 1024))
    hash_original = get_hash(TEST_FILE)

    client = DFSClient(COORD_HOST, COORD_PORT)

    # 2. Upload
    print(f"-> Upload para {REMOTE_PATH}...")
    client.upload_file(TEST_FILE, REMOTE_PATH)
    print("✅ Upload inicial concluído.")

    # 3. Injeta a falha: mata o PROCESSO do nó-alvo.
    print(f"\n☠️  Localizando e matando o processo de '{node_id}'...")
    proc = encontrar_processo_do_no(node_id)
    if proc is None:
        print(f"❌ Não encontrei um processo com '--node-id {node_id}'. O cluster está no ar?")
        _limpar()
        return
    pid = proc.pid
    try:
        proc.kill()              # SIGKILL no Linux / TerminateProcess no Windows
        proc.wait(timeout=5)
        print(f"⚠️  Processo de '{node_id}' (PID {pid}) derrubado.")
    except Exception as e:
        print(f"❌ Falha ao matar o processo: {e}")
        _limpar()
        return

    print(f"⏳ Aguardando {args.espera}s para o coordenador detectar a morte (HEARTBEAT_DEAD)...")
    time.sleep(args.espera)

    # 4. Download com o nó morto (prova do failover de leitura).
    print("\n-> Tentando o download com o nó ainda morto...")
    down_file = str(ROOT_DIR / "download_recuperado_chaos.dat")
    integro = False
    try:
        client.download_file(REMOTE_PATH, down_file)
        integro = get_hash(down_file) == hash_original
        if integro:
            print("\n✅ [SUCESSO] Arquivo baixado ÍNTEGRO mesmo com um nó morto (failover OK).")
        else:
            print("\n❌ [FALHA] Arquivo baixado, mas CORROMPIDO.")
    except Exception as e:
        print(f"\n❌ [FALHA] O cluster não entregou o arquivo. Erro: {e}")
    finally:
        # 5. Recuperação do ambiente: reinicia o processo do nó.
        print(f"\n🚀 RECUPERAÇÃO: reiniciando o processo de '{node_id}'...")
        try:
            reiniciar_no(node_id)
            print(f"✅ '{node_id}' reiniciado. Ele vai se re-registrar no coordenador.")
        except Exception as e:
            print(f"⚠️  Não consegui reiniciar '{node_id}' automaticamente: {e}")
        _limpar(down_file)

    print("\n=======================================================")
    print(f"  Failover de leitura: {'✅ OK' if integro else '❌ FALHOU'}")
    print("=======================================================\n")


def _limpar(*extras: str) -> None:
    for f in (TEST_FILE, *extras):
        try:
            if f and os.path.exists(f):
                os.remove(f)
        except OSError:
            pass


if __name__ == "__main__":
    main()
