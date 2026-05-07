"""
DESCRIÇÃO GERAL:
Script auxiliar para iniciar o nó 1 do cluster.
"""

from dfs.interface.storage_node import main


if __name__ == "__main__":
    # Sobe o nó identificado como node1.
    main(["--node-id", "node1"])