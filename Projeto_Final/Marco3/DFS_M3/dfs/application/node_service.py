"""
DESCRIÇÃO GERAL:
Esta camada representa a lógica de negócio executada dentro de cada storage
node do cluster DFS, agora adaptada ao contrato A.3 + B.2 (control plane vs
data plane). O coordenador decide; o NodeService EXECUTA localmente.

No modelo novo o nó tem dois papéis que coexistem:
  - papel PASSIVO (réplica/peer): armazena, lê, apaga e lista chunks locais.
    Usado pelo ReplicationService (StoreChunk/FetchChunk/DeleteChunk/ListChunks).
  - papel GATEWAY: quando designado pelo coordenador, atua como ingress (recebe
    o stream da CLI, fatia em chunks, replica) ou egress (junta chunks locais +
    remotos e devolve em ordem). Usado pelo DataService (UploadFile/DownloadFile).

IMPORTANTE:
O NodeService NÃO decide placement de forma autônoma — ele APLICA a regra
determinística de placement.py para saber, dado um chunk_index, quais nós são
réplicas. A decisão (a regra) é compartilhada; a aplicação (replicar de fato)
é trabalho do plano de dados.

O servicer gRPC (storage_node.py) é só um tradutor: converte stream gRPC em
chamadas a estes métodos. Toda a lógica pesada (re-fragmentação do stream em
chunks, fan-out de replicação, montagem no egress) mora AQUI.
"""

from pathlib import Path

from dfs.storage.local_storage import LocalStorage
from dfs.pb import dfs_pb2

# Convenção de armazenamento de chunk no disco local do nó. Cada chunk é um
# arquivo cujo nome é o próprio chunk_id, sob a pasta "chunks/". O .meta (hash,
# versão, timestamp) que o documento sugere é refinamento futuro — comece simples.
CHUNKS_SUBDIR = "chunks"


class NodeService:
    """
    Serviço local executado dentro de cada storage node.

    Responsabilidades (papel passivo): salvar / recuperar / remover / listar
    chunks físicos. Responsabilidades (papel gateway): orquestrar ingress e egress.
    """

    def __init__(self, storage: LocalStorage, node_id: str, shard_id: int):
        # Camada responsável pelo disco local do nó (reaproveitada do Marco 2).
        self.storage = storage
        # Usado para rastreabilidade, logs e como origin_node_id na replicação.
        self.node_id = node_id
        # Índice lógico do nó. No modelo novo, o "shard" deixa de vir de hashing
        # e passa a ser só a posição do nó na membership canônica (usada pelo
        # placement). Mantido por compatibilidade; pode virar irrelevante.
        self.shard_id = shard_id

    # =========================================================================
    # PAPEL PASSIVO — operações finas de chunk (embrulham o LocalStorage)
    # Estes são chamados pelo ReplicationService e, internamente, pelo gateway.
    # =========================================================================

    def _chunk_path(self, chunk_id: str) -> str:
        # Caminho lógico do chunk dentro da raiz do nó. O LocalStorage resolve
        # e protege contra path traversal.
        return f"{CHUNKS_SUBDIR}/{chunk_id}"

    def store_chunk(self, chunk_id: str, data: bytes) -> int:
        """
        Grava um chunk no disco local. Retorna o nº de bytes gravados.
        Usado por StoreChunk (réplica recebendo do ingress).
        """
        self.storage.put(self._chunk_path(chunk_id), data)
        return len(data)

    def read_chunk(self, chunk_id: str) -> bytes:
        """
        Lê um chunk do disco local. Levanta se não existir (o chamador trata).
        Usado por FetchChunk (peer servindo o egress) e pelo egress local.
        """
        return self.storage.get(self._chunk_path(chunk_id))

    def has_chunk(self, chunk_id: str) -> bool:
        """
        True se o chunk existe localmente. O egress usa isto para decidir o que
        tem em casa e o que precisa buscar em peers via FetchChunk.
        """
        # TODO: depende de como você expõe existência no LocalStorage. Uma forma
        # simples é tentar resolver o path e checar .exists(). Outra é olhar
        # list_chunk_ids(). Decisão tua — abaixo a versão simples por listagem.
        return chunk_id in set(self.list_chunk_ids())

    def delete_chunk(self, chunk_id: str) -> None:
        """
        Remove um chunk do disco local. Usado por DeleteChunk (coordenador no
        DELETE de arquivo ou limpeza de órfãos).
        """
        self.storage.delete(self._chunk_path(chunk_id))

    def list_chunk_ids(self) -> list[str]:
        """
        Lista os chunk_ids fisicamente presentes neste nó. É o "block report"
        do heartbeat e a resposta do ListChunks.
        """
        prefixo = f"{CHUNKS_SUBDIR}/"
        ids = []
        for caminho in self.storage.list_files():
            # list_files() devolve paths relativos à raiz (ex.: "chunks/<id>").
            # Tiramos o prefixo para devolver só o chunk_id.
            if caminho.startswith(prefixo):
                ids.append(caminho[len(prefixo):])
        return sorted(ids)

    # =========================================================================
    # PAPEL GATEWAY — ingress (PUT) e egress (GET)
    # Aqui está o MIOLO do Bloco 2. Os métodos abaixo são esqueleto: a estrutura
    # está montada, mas as decisões centrais são SUAS e estão marcadas com TODO.
    # =========================================================================

    # =========================================================================
    # =========================================================================
    def handle_upload_simples(self, request_iterator):
        """
        INCREMENTO 1 do ingress: acumula o stream inteiro e grava como UM chunk
        local. Sem fragmentação, sem fan-out, sem ConfirmUpload. Só para validar
        o caminho CLI -> ingress -> disco. Será substituído pelo handle_upload_stream.
        """
        # A primeira mensagem traz o upload_id; todas podem trazer bytes.
        upload_id = None
        buffer = bytearray()

        for msg in request_iterator:
            if msg.upload_id and upload_id is None:
                upload_id = msg.upload_id
            if msg.data:
                buffer.extend(msg.data)

        # Por enquanto, um chunk só, com índice 0.
        chunk_id = f"{upload_id}_chunk_0"
        bytes_gravados = self.store_chunk(chunk_id, bytes(buffer))

        # Retorna o que o servicer precisa pra montar o UploadResult.
        return chunk_id, bytes_gravados
    # =========================================================================
    # =========================================================================

    # =========================================================================
    # =========================================================================
    def handle_upload_fragmentado(self, request_iterator, chunk_size=None):
        """
        INCREMENTO 2 do ingress: re-fragmenta o stream em chunks de tamanho
        oficial e grava CADA chunk localmente. Ainda sem fan-out e sem
        ConfirmUpload. chunk_size é parametrizável para facilitar o teste
        (em produção, usa CHUNK_OFICIAL_SIZE do config).

        Retorna (upload_id, lista de (chunk_id, chunk_index, tamanho)).
        """
        from dfs.config import CHUNK_OFICIAL_SIZE

        if chunk_size is None:
            chunk_size = CHUNK_OFICIAL_SIZE

        upload_id = None
        buffer = bytearray()
        chunk_index = 0
        gravados = []  # (chunk_id, chunk_index, tamanho)

        def materializar(corpo):
            nonlocal chunk_index
            chunk_id = f"{upload_id}_chunk_{chunk_index}"
            self.store_chunk(chunk_id, corpo)
            gravados.append((chunk_id, chunk_index, len(corpo)))
            chunk_index += 1

        for msg in request_iterator:
            if msg.upload_id and upload_id is None:
                upload_id = msg.upload_id
            if msg.data:
                buffer.extend(msg.data)
            # Sempre que acumular um chunk oficial cheio, materializa.
            while len(buffer) >= chunk_size:
                corpo = bytes(buffer[:chunk_size])
                del buffer[:chunk_size]
                materializar(corpo)

        # O resto do buffer (menor que chunk_size) vira o último chunk.
        if buffer:
            materializar(bytes(buffer))

        return upload_id, gravados
    # =========================================================================
    # =========================================================================

    def handle_upload_stream(self, request_iterator, nodes, cluster_size):
        """
        MODO INGRESS. Recebe o stream de UploadChunk vindo da CLI, re-fragmenta
        em chunks do tamanho oficial do DFS, e para cada chunk: grava onde este
        nó for réplica + dispara StoreChunk para as DEMAIS réplicas (fan-out).

        Retorna a lista de ChunkPlacement efetivamente gravados — é o que o
        servicer vai usar para (a) responder UploadResult à CLI e (b) o
        ConfirmUpload ao coordenador.

        >>> PONTOS QUE SÃO DECISÃO SUA (TODO):

        1. RE-FRAGMENTAÇÃO. O stream chega em pedaços de transporte (ex.: 64KB),
           que NÃO são os chunks oficiais (ex.: 4MB). Você precisa acumular num
           buffer até fechar CHUNK_SIZE, materializar o chunk, e seguir. O último
           chunk é o resto do buffer quando o stream acaba. Esqueleto do laço:

               buffer = bytearray()
               chunk_index = 0
               primeira = next(request_iterator)   # traz o upload_id
               upload_id = primeira.upload_id
               buffer.extend(primeira.data)
               for msg in request_iterator:
                   buffer.extend(msg.data)
                   while len(buffer) >= CHUNK_SIZE:
                       corpo = bytes(buffer[:CHUNK_SIZE])
                       del buffer[:CHUNK_SIZE]
                       self._materializar_chunk(upload_id, chunk_index, corpo, nodes, cluster_size)
                       chunk_index += 1
               if buffer:  # resto vira o último chunk
                   self._materializar_chunk(upload_id, chunk_index, bytes(buffer), ...)

        2. CHUNK_SIZE. De onde vem? Do RegisterNodeResponse.chunk_size_bytes que
           o coordenador devolve, ou do config. Decida e seja consistente.

        3. FAN-OUT PARALELO. _materializar_chunk calcula as réplicas com
           placement.replicas_for_chunk(chunk_index, nodes, cluster_size=cluster_size).
           Se este nó está na lista -> grava local (store_chunk). Para as outras
           -> abre StoreChunk em paralelo (ThreadPoolExecutor) e espera os ACKs.
           Como tratar replicação parcial (uma réplica falhou) é decisão sua:
           aborta tudo? confirma só o que deu? O documento sugere reportar ao
           coordenador o que REALMENTE conseguiu.
        """
        raise NotImplementedError("Ingress: implementar re-fragmentação + fan-out")

    def handle_download(self, download_id, nodes, cluster_size):
        """
        MODO EGRESS. Gera (yield) os bytes do arquivo em ordem, para o servicer
        repassar à CLI como stream de DownloadChunk.

        >>> PONTOS QUE SÃO DECISÃO SUA (TODO):

        1. DE ONDE VEM A LISTA DE CHUNKS DO ARQUIVO? O egress precisa saber quais
           chunk_ids compõem o arquivo e em que ordem. No fluxo real isso vem do
           coordenador (que tem os metadados). Como o download_id chega aqui mas
           os metadados estão no coordenador, você precisa decidir: o egress
           pergunta ao coordenador via ControlService? Ou o RequestDownload já
           devolve a lista? (Isso pode exigir um ajuste combinado com a Vitória.)

        2. LOCAL vs REMOTO. Para cada chunk na ordem: se has_chunk() -> read_chunk
           local; senão -> calcula quais nós são réplicas (placement) e busca via
           FetchChunk no primeiro peer vivo.

        3. ORDEM. Os chunks DEVEM sair na ordem do arquivo (índice crescente),
           senão a CLI remonta lixo. Bufferize/ordene conforme necessário.
        """
        raise NotImplementedError("Egress: implementar montagem ordenada")