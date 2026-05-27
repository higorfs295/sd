from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DFS_DIR = ROOT_DIR / "DFS_M3"

if not DFS_DIR.exists():
    raise FileNotFoundError(f"Pasta DFS_M3 não encontrada: {DFS_DIR}")

sys.path.insert(0, str(DFS_DIR))

cli_module = importlib.import_module("dfs.interface.cli")
main = cli_module.main

if __name__ == "__main__":
    main(sys.argv[1:])
