"""
DESCRIÇÃO GERAL:
Este módulo concentra as configurações do DFS distribuído
A ideia é centralizar portas, nós e caminhos de armazenamento para evitar valores espalhados pelo código, o que facilita a manutenção e a evolução do projeto
"""

from pathlib import Path

# Raiz do projeto calculada dinamicamente a partir deste arquivo
# Isso evita caminhos fixos e deixa o projeto mais portátil entre sistemas operacionais
BASE_DIR = Path(__file__).resolve().parents[1]

# Endereço e porta do coordenador
# O coordenador é o ponto de entrada do sistema distribuído
HOST = "127.0.0.1"
PORT = 9100

# Mantém nomes explícitos para o coordenador
# Isso melhora a legibilidade quando o projeto crescer
COORDINATOR_HOST = HOST
COORDINATOR_PORT = PORT

# Define o tamanho de cada pedaço físico do arquivo
# 64 * 1024 bytes = 65536 bytes = 64 KB
# Permite dividir arquivos em chunks para sharding físico entre nós
CHUNK_SIZE = 64 * 1024

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
            "host": "127.0.0.1",
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

# Parâmetros do plano de controle (Marco 3)
# Lidos pelo coordenador para supervisionar os nós via heartbeat, e enviados aos nós no registro (RegisterNodeResponse) para que todos usem os mesmos valores.
# Os tempos abaixo são múltiplos do intervalo de heartbeat: toleram algumas perdas de batimento antes de reagir, equilibrando rapidez de detecção contra falso positivo.

# Intervalo esperado entre heartbeats de cada nó, em segundos.
HEARTBEAT_INTERVAL_SECS = 2

# Silêncio (sem heartbeat) a partir do qual o nó é reclassificado:
#  - entre SUSPECT e DEAD: SUSPECT (atrasado; ~3 batimentos perdidos)
#  - >= DEAD: DEAD (considerado fora do ar; ~7 batimentos perdidos)
HEARTBEAT_SUSPECT_SECS = 6
HEARTBEAT_DEAD_SECS = 15
