from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "DFS_M2" / "src"

if not SRC_DIR.exists():
    raise FileNotFoundError(f"Pasta src não encontrada: {SRC_DIR}")

sys.path.insert(0, str(SRC_DIR))

cli_module = importlib.import_module("dfs.interface.cli")
main = cli_module.main

if __name__ == "__main__":
    main(sys.argv[1:])