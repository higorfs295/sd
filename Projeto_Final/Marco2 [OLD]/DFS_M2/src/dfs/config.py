"""
DESCRIÇÃO GERAL:
Este módulo concentra as configurações do DFS distribuído.
A ideia é centralizar portas, nós e caminhos de armazenamento para evitar valores
espalhados pelo código, o que facilita a manutenção e a evolução do projeto.
"""

from pathlib import Path

# Raiz do projeto calculada dinamicamente.
# Isso evita caminhos fixos e deixa o projeto mais portátil entre sistemas operacionais.
BASE_DIR = Path(__file__).resolve().parents[2]

# Endereço e porta do coordenador.
# O coordenador é o ponto de entrada do sistema distribuído.
HOST = "127.0.0.1"
PORT = 9099

# Mantém nomes explícitos para o coordenador.
# Isso melhora a legibilidade quando o projeto crescer.
COORDINATOR_HOST = HOST
COORDINATOR_PORT = PORT

# Configuração estática dos nós do cluster.
# Cada nó possui um identificador, um host, uma porta e um diretório próprio.
NODES = {
    "node1": {
        "host": "127.0.0.1",
        "port": 9101,
        "storage_dir": BASE_DIR / "data" / "nodes" / "node1",
    },
    "node2": {
        "host": "127.0.0.1",
        "port": 9102,
        "storage_dir": BASE_DIR / "data" / "nodes" / "node2",
    },
    "node3": {
        "host": "127.0.0.1",
        "port": 9103,
        "storage_dir": BASE_DIR / "data" / "nodes" / "node3",
    },
}

# Ordem fixa dos nós.
# Essa ordem é importante para garantir que o shard calculado sempre aponte para o mesmo nó.
NODE_ORDER = tuple(NODES.keys())

# Quantidade total de shards.
# No Marco 2, o mais simples é ter um shard por nó.
TOTAL_SHARDS = len(NODE_ORDER)

# Mantido por compatibilidade com o Marco 1.
# Pode servir como raiz padrão em testes simples ou execuções isoladas.
STORAGE_DIR = BASE_DIR / "data" / "storage"