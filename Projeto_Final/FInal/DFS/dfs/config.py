"""
DESCRIÇÃO GERAL:
Este módulo concentra as configurações do DFS distribuído
A ideia é centralizar portas, nós e caminhos de armazenamento para evitar valores espalhados pelo código, o que facilita a manutenção e a evolução do projeto
"""

from pathlib import Path
import os
# Raiz do projeto calculada dinamicamente a partir deste arquivo
# Isso evita caminhos fixos e deixa o projeto mais portátil entre sistemas operacionais
BASE_DIR = Path(__file__).resolve().parents[1]

# Endereço e porta  do coordenador
# O coordenador é o ponto de entrada do sistema distribuído
HOST = os.getenv("COORDINATOR_HOST", "127.0.0.1")
PORT = 9100

# Mantém nomes explícitos para o coordenador
# Isso melhora a legibilidade quando o projeto crescer
COORDINATOR_HOST = HOST
COORDINATOR_PORT = PORT

# Tamanho do chunk do DFS: a unidade de placement e replicação.
# É este valor que define em quantos chunks um arquivo é cortado, e quantas entradas de metadado e quantas rodadas de replicação ele gera.
# Tamanho do chunk do DFS: a unidade de placement e replicação.
# É este valor que define em quantos chunks um arquivo é cortado, e quantas entradas de metadado e quantas rodadas de replicação ele gera.
# Valor mínimo e máximo são definidos para evitar chunks muito pequenos (muitos metadados e overhead) ou muito grandes (memória e stragglers).
MIN_CHUNK_SIZE = 4 * 1024 * 1024
MAX_CHUNK_SIZE = 64 * 1024 * 1024  # 64 MB: teto padrão GFS/HDFS (memória e straggler)

# O tamanho do chunk é calculado dinamicamente a partir do tamanho do arquivo e da quantidade de nós, para balancear o número de chunks e evitar desequilíbrio.
CHUNK_TARGET_MULTIPLIER = (
    3  # alvo = 3 × nº de nós (over-partitioning, evita desequilíbrio)
)
# Tamanho do PEDAÇO DE TRANSPORTE do stream: quanto a CLI envia por mensagem gRPC ao subir/baixar um arquivo.
# NÃO é unidade de placement nem de replicação, é só transporte.
# Pequeno de propósito: mantém o uso de memória baixo e fica bem abaixo do limite default de mensagem do gRPC (4 MB).
STREAM_SIZE = 64 * 1024  # 64 KB

# Define a pasta principal de dados do sistema
DATA_DIR = BASE_DIR / "data"

# Define a pasta onde os metadados do DFS serão armazenados
# Metadados são informações como: arquivo X possui chunks nos nós Y e Z
METADATA_DIR = DATA_DIR / "metadata"

# Define o arquivo JSON que guardará o índice persistente de metadados
METADATA_FILE = METADATA_DIR / "metadata_index.json"

# Mantido por compatibilidade com o Marco 1
# Pode servir como raiz padrão em testes simples ou execuções isoladas
STORAGE_DIR = DATA_DIR / "storage"

# Quantidade de nós do cluster
# Para mudar, edite o valor abaixo e reinicie o cluster (run_cluster.py)
# As portas e os diretórios são alocados automaticamente a partir dos valores BASE_NODE_PORT e DATA_DIR.
# IMPORTANTE: ao mudar este valor com dados já gravados em disco, os arquivos antigos podem ficar inacessíveis (porque o hash do sharding redistribui as posições)
# Recomenda-se apagar a pasta 'data/' antes de mudar o número de nós
NODE_COUNT = 5

# Porta do primeiro nó
# A regra de alocação é: node1 -> 9101, node2 -> 9102, node3 -> 9103, ...
BASE_NODE_PORT = 9101


def build_nodes(count: int, base_port: int = BASE_NODE_PORT) -> dict[str, dict]:
    """
    Gera dinamicamente a configuração dos nós do cluster

    Cada nó recebe:
    - identificador sequencial no formato "nodeN" (node1, node2, ...);
    - porta calculada a partir de base_port (uma porta a mais por nó);
    - diretório próprio dentro de DATA_DIR/nodes/

    Mudar a quantidade de nós significa apenas alterar a constante NODE_COUNT acima
    O restante do sistema (run_cluster.py, sharding, registry) lê NODES e NODE_ORDER e se adapta automaticamente
    """
    return {
        f"node{i}": {
            "host": os.getenv(f"node{i}_HOST".upper(), os.getenv("NODE_HOST", "127.0.0.1")),
            "port": base_port + i - 1,
            "storage_dir": DATA_DIR / "nodes" / f"node{i}",
        }
        for i in range(1, count + 1)
    }


# Configuração final dos nós, gerada a partir de NODE_COUNT
NODES = build_nodes(NODE_COUNT)

# Ordem fixa dos nós
# Essa ordem é importante para garantir que o shard calculado sempre aponte para o mesmo nó
NODE_ORDER = tuple(NODES.keys())

# Quantidade total de shards
# No Marco 2, o mais simples é ter um shard por nó
TOTAL_SHARDS = len(NODE_ORDER)


# Intervalo esperado entre heartbeats de cada nó, em segundos.
HEARTBEAT_INTERVAL = 2

# Silêncio (sem heartbeat) a partir do qual o nó é reclassificado:
#  - entre SUSPECT e DEAD: SUSPECT (atrasado; ~4 batimentos perdidos)
#  - >= DEAD: DEAD (considerado fora do ar; ~10 batimentos perdidos)
HEARTBEAT_SUSPECT = 8
HEARTBEAT_DEAD = 20

# Quantidade de réplicas de cada chunk.
REPLICATION_FACTOR = 3

# Intervalo da varredura ativa de re-replicação.
# A thread do ReplicationWatcher acorda a cada WATCHER_INTERVAL segundos para detectar transições para DEAD.
# Escolhido igual ao HEARTBEAT_INTERVAL: a latência total de detecção de uma morte é, no pior caso, HEARTBEAT_DEAD + WATCHER_INTERVAL (≈ 10 s)
WATCHER_INTERVAL = 2
