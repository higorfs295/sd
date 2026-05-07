from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DFS_DIR = ROOT_DIR / "DFS_M2"


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
    Sobe os três nós e o coordenador do DFS.
    """
    if not DFS_DIR.exists():
        print(f"Erro: pasta não encontrada: {DFS_DIR}")
        sys.exit(1)

    env = build_env()
    processes: list[subprocess.Popen] = []

    try:
        processes.append(
            start_process(
                "node1",
                [
                    sys.executable,
                    "-m",
                    "dfs.interface.storage_node",
                    "--node-id",
                    "node1",
                ],
                cwd=DFS_DIR,
                env=env,
            )
        )
        time.sleep(0.5)

        processes.append(
            start_process(
                "node2",
                [
                    sys.executable,
                    "-m",
                    "dfs.interface.storage_node",
                    "--node-id",
                    "node2",
                ],
                cwd=DFS_DIR,
                env=env,
            )
        )
        time.sleep(0.5)

        processes.append(
            start_process(
                "node3",
                [
                    sys.executable,
                    "-m",
                    "dfs.interface.storage_node",
                    "--node-id",
                    "node3",
                ],
                cwd=DFS_DIR,
                env=env,
            )
        )
        time.sleep(0.5)

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
