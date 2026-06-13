"""
DESCRIÇÃO GERAL:
Catálogo de nós do cluster DFS.

Tem DUAS responsabilidades, separadas de propósito:

1. MEMBERSHIP CANÔNICA (estática): a lista fixa dos N nós que o cluster PODE ter, lida de config.py, sempre na mesma ordem.
   É ela que o placement.py usa (a regra round-robin EXIGE a lista canônica completa, nunca só os vivos).
   Esta parte NÃO muda em tempo de execução.

2. ESTADO DINÂMICO (vivo): quem está de fato ligado AGORA.
   Cada nó se anuncia (register_node) e manda um batimento periódico (record_heartbeat).
   A partir do tempo desde o último batimento, classificamos o nó como ALIVE, SUSPECT ou DEAD.
   Esta parte muda o tempo todo.

Por que separar canônica de vivos:

1. PLACEMENT (decidir onde gravar um chunk novo): usa a canônica.
   - Inclui nós temporariamente DOWN.
    Por quê? Porque um nó pode estar em manutenção rápida (reiniciando) e ainda deve receber sua cota de chunks.
    O sistema decide o placement, a re-replicação eventual entrega os bytes quando o nó voltar.

2. ROTEAMENTO PONTUAL (escolher ingress, escolher egress): usa só vivos.
   - Não dá para mandar um cliente falar com um nó morto agora.

3. PLACEMENT JAMAIS É RECALCULADO.
    A verdade sobre onde cada chunk está vive nos metadados (ChunkPlacement.replicas).
    GET, DELETE e re-replicação consultam os metadados, nunca recalculam.

Portanto, mesmo que a canônica cresça com o tempo (nós novos viaRegisterNode), chunks antigos permanecem inalterados.
É essa propriedade que viabiliza elasticidade sem movimentação massiva de dados.
"""

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from dfs.config import (
    NODES,
    NODE_ORDER,
    HEARTBEAT_SUSPECT,
    HEARTBEAT_DEAD,
)

# Importamos dfs_pb2 só para reutilizar a enum NodeStatus (ALIVE/SUSPECT/DEAD)
# já definida no .proto, em vez de inventar uma lista de status paralela.
from dfs.pb import dfs_pb2


@dataclass(frozen=True)  # impede alterações acidentais após a criação
class NodeInfo:
    """
    Identidade ESTÁTICA de um nó (vem do config; não muda).
    """

    node_id: str
    host: str
    port: int
    storage_dir: Path


@dataclass
class NodeRuntime:
    """
    Estado DINÂMICO de um nó, atualizado a cada registro/heartbeat.
    Ao contrário do NodeInfo (identidade fixa, frozen), aqui os campos mudam o tempo todo conforme os batimentos chegam.
    """

    node_id: str
    host: str
    port: int
    free_space_bytes: int = 0
    active_uploads: int = 0
    active_downloads: int = 0
    # "Block report": os chunks que o nó disse possuir no último heartbeat.
    chunk_ids: list[str] = field(default_factory=list)
    # Momento do último sinal de vida, medido com time.monotonic().
    # 0.0 = "nunca deu sinal".
    last_heartbeat: float = 0.0


class NodeRegistry:
    """
    Mantém a membership canônica (estática) e o estado vivo (dinâmico) do cluster.
    """

    def __init__(self, nodes: dict[str, dict] | None = None):
        """
        Inicializa o registro de nós.
        Se nenhum conjunto de nós for passado manualmente, utiliza a configuração global definida em config.py.
        """

        raw_nodes = nodes or NODES

        # Parte 1: Estática
        # Converte o dicionário bruto em objetos NodeInfo
        self._nodes: dict[str, NodeInfo] = {
            node_id: NodeInfo(
                node_id=node_id,
                host=spec["host"],
                port=spec["port"],
                storage_dir=Path(spec["storage_dir"]),
            )
            for node_id, spec in raw_nodes.items()
        }

        # Ordem fixa e previsível (base do determinismo do placement)
        # Isso é importante para o cálculo consistente de shards.
        self._ordered_ids = [nid for nid in NODE_ORDER if nid in self._nodes]

        # Fallback de segurança:
        # se NODE_ORDER estiver vazio por algum motivo, usa a ordem natural dos nós disponíveis.
        if not self._ordered_ids:
            self._ordered_ids = list(self._nodes.keys())

        # Parte 2: Dinâmico
        # Tabela de runtime, preenchida por register_node/record_heartbeat.
        self._runtime: dict[str, NodeRuntime] = {}

        # Lock: o servidor gRPC atende em várias threads ao mesmo tempo, então vários nós podem mexer nesta tabela simultaneamente.
        # O cadeado garante que uma operação termine antes da próxima começar (mesma ideia do metadata_service.py).
        # Sem ele, duas escritas concorrentes poderiam se corromper.
        self._lock = threading.Lock()

    # PARTE ESTÁTICA (usada pelo placement)

    def canonical_members(self) -> list[NodeInfo]:
        """Todos os nós conhecidos pelo cluster, na ordem fixa. É o que o placement consome."""
        return [self._nodes[nid] for nid in self._ordered_ids]

    def get(self, node_id: str) -> NodeInfo:
        """Busca um nó pelo ID. Ex.: registry.get('node1')."""
        return self._nodes[node_id]

    def get_by_index(self, index: int) -> NodeInfo:
        """
        Retorna um nó pela posição lógica no cluster.

        O uso de módulo (%) permite:
        - rotação circular;
        - fallback simples;
        - round-robin determinístico.
        """

        node_id = self._ordered_ids[index % len(self._ordered_ids)]
        return self._nodes[node_id]

    def index_of(self, node_id: str) -> int:
        """Índice lógico de um nó na ordem registrada."""
        return self._ordered_ids.index(node_id)

    def size(self) -> int:
        """Quantidade de nós registrados."""
        return len(self._ordered_ids)

    # PARTE DINÂMICA: registro, heartbeat e detecção de falha

    def register_node(
        self, node_id: str, host: str, port: int, free_space_bytes: int
    ) -> None:
        """
        Registra (ou re-registra) um nó.
        Chamado pela RPC RegisterNode quando um nó liga.
        O próprio registro já é um sinal de vida, então marcamos o last_heartbeat = agora.
        Re-registrar (ex.: nó reiniciou) sobrescreve.
        """
        with self._lock:
            self._runtime[node_id] = NodeRuntime(
                node_id=node_id,
                host=host,
                port=port,
                free_space_bytes=free_space_bytes,
                last_heartbeat=time.monotonic(),
            )

    def record_heartbeat(
        self,
        node_id: str,
        free_space_bytes: int,
        active_uploads: int,
        active_downloads: int,
        chunk_ids: list[str],
    ) -> bool:
        """
        Registra um heartbeat. Chamado pela RPC Heartbeat a cada 2s.
        Atualiza os campos do nó e, principalmente, o last_heartbeat.

        Retorna True se o nó é conhecido;
        False se for um nó totalmente desconhecido (nem registrado, nem presente na membership canônica).
        """
        with self._lock:
            runtime = self._runtime.get(node_id)

            if runtime is None:
                # Heartbeat de um nó que não se registrou antes.
                # Se ele existe na config, criamos a entrada usando o endereço do config (bootstrap).
                # Senão, é desconhecido: recusa. Por isso marca como False, para o nó se registrar primeiro.
                if node_id not in self._nodes:
                    return False
                info = self._nodes[node_id]
                runtime = NodeRuntime(node_id=node_id, host=info.host, port=info.port)
                self._runtime[node_id] = runtime

            runtime.free_space_bytes = free_space_bytes
            runtime.active_uploads = active_uploads
            runtime.active_downloads = active_downloads
            runtime.chunk_ids = list(chunk_ids)
            runtime.last_heartbeat = time.monotonic()
            return True

    def alive_members(self) -> list[NodeInfo]:
        """
        Retorna os nós da membership canônica que estão ALIVE agora.

        Usado para ROTEAMENTO PONTUAL: escolher ingress e egress.
        Isso é feito pois não dá para mandar um cliente falar com um nó morto.

        Difere de canonical_members():
        - canonical_members(): placement (inclui nós SUSPECT e DEAD, porque o placement é estático e não pode mudar a cada heartbeat)
        - alive_members(): ingress/egress (só quem está vivo agora)

        Classifica todos os nós dentro de um único bloco com lock, para evitar que o estado mude entre classificações.
        """
        with self._lock:
            return [
                self._nodes[node_id]
                for node_id in self._ordered_ids
                # _status_locked: versão sem lock, chamada dentro do lock já ativo.
                # Chamar status_of aqui causaria deadlock.
                if self._status_locked(node_id) == dfs_pb2.NODE_STATUS_ALIVE
            ]

    # Difere de alive_members(), que devolve NodeInfo (id, host, porta), aqui devolve NodeRuntime (free_space_bytes, uploads ativos, etc.).
    def alive_runtimes(self) -> list[NodeRuntime]:
        """
        Retorna o estado vivo (NodeRuntime) dos nós que estão com status ALIVE agora.
        É o que a re-replicação consome para escolher o destino de uma cópia (o nó vivo com mais espaço livre).

        Classifica todos os nós dentro de um único bloco com lock, para evitar que o estado mude entre classificações.
        """
        with self._lock:
            return [
                runtime
                for node_id, runtime in self._runtime.items()
                # _status_locked: versão sem lock, chamada dentro do lock já ativo.
                # Chamar status_of aqui causaria deadlock.
                if self._status_locked(node_id) == dfs_pb2.NODE_STATUS_ALIVE
            ]

    def status_of(self, node_id: str) -> int:
        """
        Classifica o nó pelo tempo desde o último batimento
        (cálculo preguiçoso: feito na hora da pergunta, sem thread de fundo).

        Devolve um valor da enum NodeStatus do .proto:
        - nunca deu sinal        → NODE_STATUS_DEAD
        - silêncio < SUSPECT     → NODE_STATUS_ALIVE
        - SUSPECT <= silêncio < DEAD → NODE_STATUS_SUSPECT
        - silêncio >= DEAD       → NODE_STATUS_DEAD

        Versão pública: pega o lock, delega para _status_locked, libera.
        Quem já segura o lock deve chamar _status_locked diretamente.
        """
        with self._lock:
            return self._status_locked(node_id)

    def _status_locked(self, node_id: str) -> int:
        """
        Versão interna de status_of, SEM pegar o lock.
        Quem chama PRECISA já estar segurando self._lock.

        Existe para permitir que métodos como alive_members() classifiquem
        vários nós em sequência dentro de um único bloco `with self._lock:`,
        sem cair em deadlock por tentar pegar o mesmo lock duas vezes.
        """
        runtime = self._runtime.get(node_id)
        if runtime is None or runtime.last_heartbeat == 0.0:
            return dfs_pb2.NODE_STATUS_DEAD

        silencio = time.monotonic() - runtime.last_heartbeat

        if silencio < HEARTBEAT_SUSPECT:
            return dfs_pb2.NODE_STATUS_ALIVE
        if silencio < HEARTBEAT_DEAD:
            return dfs_pb2.NODE_STATUS_SUSPECT
        return dfs_pb2.NODE_STATUS_DEAD

    def list_runtime(self) -> list[NodeRuntime]:
        """
        Cópia da tabela de estado vivo (para diagnóstico/listagem).
        Devolve uma cópia da lista para o chamador não mexer na tabela interna por acidente.
        """
        with self._lock:
            return list(self._runtime.values())
