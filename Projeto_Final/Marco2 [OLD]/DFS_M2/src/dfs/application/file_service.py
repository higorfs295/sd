"""
DESCRIÇÃO GERAL:
Esta camada representa o serviço do coordenador.
Ela recebe a requisição da CLI, decide qual nó deve atender a operação e encaminha
a mensagem para o destino correto.
"""

from dfs.cluster.node_client import NodeClient
from dfs.cluster.node_registry import NodeRegistry
from dfs.cluster.shard_manager import ShardManager
from dfs.protocol import parse_request, make_request, make_response


class FileService:
    """
    Serviço central do coordenador.
    """

    def __init__(
        self,
        registry: NodeRegistry | None = None,
        shard_manager: ShardManager | None = None,
        timeout: float = 5.0,
    ):
        # Cadastro dos nós conhecidos.
        self.registry = registry or NodeRegistry()

        # Mapeamento de shard.
        self.shard_manager = shard_manager or ShardManager(self.registry)

        # Timeout padrão para comunicação entre nós.
        self.timeout = timeout

    def _forward_to_node(self, node, request_op: str, path: str, data: bytes = b""):
        """
        Encaminha a operação para o nó responsável.
        """
        # Calcula o shard associado ao caminho.
        shard_id = self.shard_manager.shard_id_for_path(path)

        # Cria cliente interno para falar com o nó.
        client = NodeClient(node.host, node.port, timeout=self.timeout)

        # Monta a mensagem que será enviada ao nó.
        raw_request = make_request(
            op=request_op,
            path=path,
            data=data,
            node_id=node.node_id,
            shard_id=shard_id,
        )

        # Envia a requisição já serializada.
        return client.send_raw(raw_request), shard_id

    def dispatch(self, raw_request: bytes) -> bytes:
        """
        Processa a requisição recebida pelo coordenador.
        """
        # Desserializa a requisição da CLI.
        request = parse_request(raw_request)

        # Normaliza a operação.
        op = request.op.upper().strip()

        try:
            if op in {"PUT", "GET", "DELETE"}:
                # Essas operações dependem do caminho do arquivo.
                if not request.path:
                    return make_response(
                        False,
                        "Caminho vazio",
                        node_id="coordinator",
                        shard_id=-1,
                    )

                # Descobre qual nó deve atender a operação.
                node = self.shard_manager.node_for_path(request.path)

                # Encaminha a operação para o nó responsável.
                response, shard_id = self._forward_to_node(
                    node=node,
                    request_op=op,
                    path=request.path,
                    data=request.data,
                )

                # Reempacota a resposta para incluir metadados consistentes.
                return make_response(
                    response.ok,
                    response.message,
                    data=response.data,
                    entries=list(response.entries),
                    node_id=response.node_id or node.node_id,
                    shard_id=shard_id,
                )

            if op == "LIST":
                # Lista distribuída: consulta todos os nós e junta as respostas.
                all_entries: list[str] = []
                any_ok = False

                for node in self.registry.list_nodes():
                    client = NodeClient(node.host, node.port, timeout=self.timeout)

                    # Cada nó recebe um LIST independente.
                    raw_list_request = make_request(
                        op="LIST",
                        node_id=node.node_id,
                        shard_id=self.shard_manager.shard_id_for_node(node.node_id),
                    )

                    try:
                        response = client.send_raw(raw_list_request)

                        # Se o nó respondeu corretamente, acumula os arquivos.
                        if response.ok:
                            any_ok = True
                            all_entries.extend(response.entries)
                    except Exception:
                        # Se um nó falhar, o coordenador continua consultando os demais.
                        continue

                # Remove duplicatas e organiza a saída.
                unique_entries = sorted(dict.fromkeys(all_entries))

                if not any_ok and not unique_entries:
                    return make_response(
                        False,
                        "Falha ao consultar os nós para listagem",
                        node_id="coordinator",
                        shard_id=-1,
                    )

                return make_response(
                    True,
                    "Listagem distribuída concluída",
                    entries=unique_entries,
                    node_id="coordinator",
                    shard_id=-1,
                )

            # Operação inválida.
            return make_response(
                False,
                "Operação inválida",
                node_id="coordinator",
                shard_id=-1,
            )

        except Exception as exc:
            # Qualquer erro inesperado é transformado em resposta controlada.
            return make_response(
                False,
                f"Erro: {exc}",
                node_id="coordinator",
                shard_id=-1,
            )