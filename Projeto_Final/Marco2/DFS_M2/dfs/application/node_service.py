"""
DESCRIÇÃO GERAL:
Esta camada representa a lógica de negócio executada
dentro de cada storage node do cluster DFS.

O coordenador:
- recebe operações do cliente;
- decide para qual nó enviar;
- distribui chunks.

Já o NodeService:
- executa a operação LOCALMENTE;
- manipula o storage físico;
- devolve respostas padronizadas.

IMPORTANTE:
O NodeService NÃO toma decisões de sharding.
Ele apenas executa operações locais.
"""

from dfs.pb.protocol import (
    make_response,
    parse_request,
)

from dfs.storage.local_storage import LocalStorage


class NodeService:
    """
    Serviço local executado dentro de cada storage node.

    Responsabilidades:
    - salvar chunks;
    - recuperar chunks;
    - remover chunks;
    - criar diretórios físicos;
    - remover diretórios físicos;
    - listar conteúdo físico do nó.
    """

    def __init__(
        self,
        storage: LocalStorage,
        node_id: str,
        shard_id: int,
    ):
        """
        Inicializa o serviço do nó.
        """

        # ========================================================
        # STORAGE LOCAL
        # ========================================================

        # Camada responsável pelo disco local do nó.
        self.storage = storage

        # ========================================================
        # IDENTIFICAÇÃO DO NÓ
        # ========================================================

        # Usado para rastreabilidade e debug.
        self.node_id = node_id

        # Índice lógico do shard deste nó.
        self.shard_id = shard_id

    def dispatch(self, raw_request: bytes) -> bytes:
        """
        Processa uma requisição recebida pela rede.

        Fluxo:
        1) desserializa protobuf;
        2) identifica operação;
        3) executa operação local;
        4) devolve resposta protobuf.
        """

        # ========================================================
        # PARSE DA REQUISIÇÃO
        # ========================================================

        request = parse_request(raw_request)

        # Normaliza o nome da operação.
        op = request.op.upper().strip()

        try:

            # ====================================================
            # PUT
            # ====================================================

            if op == "PUT":
                """
                Salva um arquivo/chunk localmente.
                """

                self.storage.put(
                    request.path,
                    request.data,
                )

                return make_response(
                    True,
                    "Arquivo salvo com sucesso",
                    node_id=self.node_id,
                    shard_id=self.shard_id,
                )

            # ====================================================
            # GET
            # ====================================================

            if op == "GET":
                """
                Recupera um arquivo/chunk localmente.
                """

                data = self.storage.get(request.path)

                return make_response(
                    True,
                    "Arquivo encontrado",
                    data=data,
                    node_id=self.node_id,
                    shard_id=self.shard_id,
                )

            # ====================================================
            # DELETE
            # ====================================================

            if op == "DELETE":
                """
                Remove um arquivo/chunk localmente.
                """

                self.storage.delete(request.path)

                return make_response(
                    True,
                    "Arquivo removido com sucesso",
                    node_id=self.node_id,
                    shard_id=self.shard_id,
                )

            # ====================================================
            # MKDIR
            # ====================================================

            if op == "MKDIR":
                """
                Cria um diretório físico local.
                """

                self.storage.mkdir(request.path)

                return make_response(
                    True,
                    "Diretório criado com sucesso",
                    node_id=self.node_id,
                    shard_id=self.shard_id,
                )

            # ====================================================
            # RMDIR
            # ====================================================

            if op == "RMDIR":
                """
                Remove um diretório físico local.

                IMPORTANTE:
                Apenas diretórios vazios podem ser removidos.
                """

                self.storage.rmdir(request.path)

                return make_response(
                    True,
                    "Diretório removido com sucesso",
                    node_id=self.node_id,
                    shard_id=self.shard_id,
                )

            # ====================================================
            # LIST
            # ====================================================

            if op == "LIST":
                """
                IMPORTANTE:
                Este LIST NÃO é o LIST lógico do DFS.

                O LIST do cliente:
                - é resolvido no coordenador;
                - consulta metadata;
                - retorna namespace lógico.

                Já este LIST:
                - é administrativo;
                - lista arquivos físicos;
                - mostra chunks reais presentes no nó.
                """

                entries = self.storage.list_files()

                return make_response(
                    True,
                    "Listagem concluída",
                    entries=entries,
                    node_id=self.node_id,
                    shard_id=self.shard_id,
                )

            # ====================================================
            # OPERAÇÃO INVÁLIDA
            # ====================================================

            return make_response(
                False,
                f"Operação inválida: {op}",
                node_id=self.node_id,
                shard_id=self.shard_id,
            )

        except Exception as exc:
            """
            Qualquer erro local é convertido
            em resposta controlada.
            """

            return make_response(
                False,
                f"Erro local no nó {self.node_id}: {exc}",
                node_id=self.node_id,
                shard_id=self.shard_id,
            )