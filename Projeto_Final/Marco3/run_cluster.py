from __future__ import annotations

import os
import subprocess
import sys
import time
import importlib
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DFS_DIR = ROOT_DIR / "DFS_M3"

# Adiciona DFS_M2/ ao sys.path para que este script consiga importar o pacote 'dfs'
# Precisa vir ANTES do import de dfs.config
sys.path.insert(0, str(DFS_DIR))

from dfs.config import NODE_ORDER  # type: ignore[import-not-found]


def build_env() -> dict[str, str]:
    """
    Monta o ambiente para que os subprocessos encontrem o pacote dfs.
    """
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")

    paths = [str(DFS_DIR)]
    if current_pythonpath:
        paths.append(current_pythonpath)

    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def start_process(
    label: str, args: list[str], cwd: Path, env: dict[str, str]
) -> subprocess.Popen:
    """
    Inicia um processo filho e devolve o handle dele.
    """
    print(f"[INICIANDO] {label}: {' '.join(args)}")
    return subprocess.Popen(
        args,
        cwd=str(cwd),
        env=env,
    )


def main() -> None:
    """
    Sobe os três nós e o coordenador do DFS
    """
    if not DFS_DIR.exists():
        print(f"Erro: pasta não encontrada: {DFS_DIR}")
        sys.exit(1)

    env = build_env()
    processes: list[subprocess.Popen] = []

    try:
        # Loop que cobre quantos nós existirem em config.py
        # Se adicionar nodeX no NODES, eles sobem automaticamente, sem mexer aqui
        for node_id in NODE_ORDER:
            processes.append(
                start_process(
                    node_id,
                    [
                        sys.executable,
                        "-m",
                        "dfs.interface.storage_node",
                        "--node-id",
                        node_id,
                    ],
                    cwd=DFS_DIR,
                    env=env,
                )
            )
            # Pequena pausa entre subidas para evitar disputa pela porta
            time.sleep(0.5)

        # Coordenador sobe DEPOIS dos nós, assim quando ele começa a processar requisições, todos os nós já estão prontos
        processes.append(
            start_process(
                "coordinator",
                [sys.executable, "-m", "dfs.interface.server"],
                cwd=DFS_DIR,
                env=env,
            )
        )

        print("\nCluster DFS iniciado.")
        print("Deixe este terminal aberto enquanto usa a CLI em outro terminal.")
        print("Para encerrar, pressione Ctrl+C.\n")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nEncerrando cluster...")

    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()

        for proc in processes:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

        print("Cluster encerrado.")


if __name__ == "__main__":
    main()
