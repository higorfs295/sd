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

from dfs.storage.local_storage import LocalStorage
from dfs.pb import dfs_pb2  # Importamos diretamente as mensagens geradas


class NodeService:
    """
    Serviço local executado dentro de cada storage node.

    Responsabilidades:
    - salvar chunks;
    - recuperar chunks;
    - remover chunks;
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

    def dispatch(self, request: dfs_pb2.FileRequest) -> dfs_pb2.FileResponse:
        """
        Processa uma requisição recebida pela rede (agora via gRPC).

        Fluxo:
        1) recebe objeto protobuf;
        2) identifica operação;
        3) executa operação local;
        4) devolve resposta protobuf.
        """

        # Normaliza o nome da operação (o request já vem parseado pelo gRPC)
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

                return dfs_pb2.FileResponse(
                    ok=True,
                    message="Arquivo salvo com sucesso",
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

                return dfs_pb2.FileResponse(
                    ok=True,
                    message="Arquivo encontrado",
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

                return dfs_pb2.FileResponse(
                    ok=True,
                    message="Arquivo removido com sucesso",
                    node_id=self.node_id,
                    shard_id=self.shard_id,
                )

            # ====================================================
            # LIST
            # ====================================================
            if op == "LIST":
                """
                Lista arquivos físicos (chunks reais presentes no nó).
                """
                entries = self.storage.list_files()

                return dfs_pb2.FileResponse(
                    ok=True,
                    message="Listagem concluída",
                    entries=entries,  # O Protobuf aceita listas Python diretas para campos 'repeated'
                    node_id=self.node_id,
                    shard_id=self.shard_id,
                )

            # ====================================================
            # OPERAÇÃO INVÁLIDA
            # ====================================================
            return dfs_pb2.FileResponse(
                ok=False,
                message=f"Operação inválida: {op}",
                node_id=self.node_id,
                shard_id=self.shard_id,
            )

        except Exception as exc:
            """
            Qualquer erro local é convertido em resposta controlada.
            """
            return dfs_pb2.FileResponse(
                ok=False,
                message=f"Erro local no nó {self.node_id}: {exc}",
                node_id=self.node_id,
                shard_id=self.shard_id,
            )