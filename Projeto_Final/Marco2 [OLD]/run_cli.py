from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
DFS_SRC_DIR = ROOT_DIR / "DFS_M2" / "src"

# Faz o Python enxergar o pacote dfs sem precisar entrar manualmente em DFS_M2
sys.path.insert(0, str(DFS_SRC_DIR))

from dfs.interface.cli import main  # noqa: E402


if __name__ == "__main__":
    main(sys.argv[1:])