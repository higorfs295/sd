"""
DESCRIÇÃO GERAL:
Processo do coordenador do DFS (servidor gRPC).

O coordenador implementa o ControlService (plano de controle): registro de nós,
heartbeat, autorização de upload/download, deleção e listagem. NUNCA toca em
bytes de arquivos de usuário, pois isso é responsabilidade dos nós (DataService).
"""

import grpc
import threading
import uuid
import math
from concurrent import futures

from dfs.config import COORDINATOR_HOST, COORDINATOR_PORT
from dfs.application.metadata_service import MetadataService
from dfs.cluster.node_registry import NodeRegistry
from dfs.config import (
    COORDINATOR_HOST,
    COORDINATOR_PORT,
    CHUNK_SIZE,
    HEARTBEAT_INTERVAL,
    REPLICATION_FACTOR,
)
from dfs.pb import dfs_pb2, dfs_pb2_grpc
from dfs.cluster import placement
from dfs.cluster import replication_client
from dfs.cluster.replication_watcher import ReplicationWatcher

# ====================================================================== #
# CONTROLSERVICE: Plano de controle do coordenador
# ====================================================================== #


class ControlServiceServicer(dfs_pb2_grpc.ControlServiceServicer):
    """
    Implementa o ControlService do dfs.proto.

    Herdar de dfs_pb2_grpc.ControlServiceServicer significa que esta classe
    promete responder a todas as RPCs do serviço. O gRPC chama o método Python
    com o MESMO nome da RPC (ListFiles, RegisterNode, ...).

    Toda RPC unária tem a forma def NomeDaRpc(self, request, context):
      - request → mensagem de entrada já desserializada pelo gRPC;
      - context → contexto da chamada (define status/erro, lê deadline, etc.);
      - retorno → uma instância da mensagem de saída declarada no .proto.
    """

    def __init__(
        self,
        metadata: MetadataService | None = None,
        registry: NodeRegistry | None = None,
    ):
        """
        Inicializa o servicer do plano de controle e recebe duas dependências de fora (injeção de dependência):

        - metadata: o MetadataService, que guarda o índice de arquivos em disco (data/metadata/).
        É a fonte sobre quais arquivos existem e onde seus chunks estão.
        Usado pelo ListFiles, RequestUpload, ConfirmUpload, RequestDownload, DeleteFile.

        - registry: o NodeRegistry, que mantém o catálogo dos nós do cluster,
        tanto a membership canônica (todos os nós conhecidos) quanto o estado dinâmico (quem está vivo agora, via heartbeat).
        Usado pelo RegisterNode e pelo Heartbeat.

        Receber as dependências como parâmetro (em vez de instanciar fixo) permite que um teste passe versões falsas/controladas.
        Por exemplo, um MetadataService apontando para um índice de teste. Se nada for passado, criamos as instâncias padrão com o 'or'.
        """
        # Se 'metadata' veio preenchido, usa ele; senão, cria um MetadataService padrão.
        self.metadata = metadata or MetadataService()

        # Usa o que veio, ou cria um NodeRegistry padrão (que lê a membership canônica do config.py na inicialização).
        self.registry = registry or NodeRegistry()

        # Registro de uploads pendentes: upload_id com metadados do upload.
        # O ConfirmUpload vai consultar aqui para validar que o upload_id existe e recuperar os dados necessários para gravar os metadados.
        # Dicionário protegido por lock próprio: o gRPC atende em múltiplas threads e dois uploads simultâneos podem chegar ao mesmo tempo.
        self._uploads_pendentes: dict[str, dict] = {}
        self._lock_uploads = threading.Lock()

    def ListFiles(self, request, context):
        """
        Devolve a lista de arquivos conhecidos pelo coordenador.

        Contrato:
            rpc ListFiles (ListFilesRequest) returns (ListFilesResponse);
            message FileEntry {
                string logical_path=1; // caminho lógico do arquivo
                int64 total_size_bytes=2; // tamanho total do arquivo em bytes
                int32 chunk_count=3; // número de chunks do arquivo
                repeated string nodes_used=4; // nós que possuem chunks do arquivo
            }
        """
        # 1) Caminhos já indexados (chaves ordenadas alfabeticamente).
        caminhos = self.metadata.list_files()

        # 2) Monta uma FileEntry por arquivo.
        entradas = []
        for caminho in caminhos:
            # Dicionário salvo pelo MetadataService:
            #   {"path","size","chunks":[...],
            #    "distribution":{"chunk_count","nodes_used":[...]}}
            info = self.metadata.get_file(caminho)
            if info is None:
                # Defensivo: pode ter sido removido entre o list e o get.
                continue

            distribuicao = info.get("distribution", {})

            # 3) Cada argumento aqui deve ter o nome EXATO do campo no .proto.
            entradas.append(
                dfs_pb2.FileEntry(
                    logical_path=info["path"],
                    total_size_bytes=info.get("total_size_bytes")
                    or info.get(
                        "size", 0
                    ),  # compatibilidade com o formato antigo, que usava "size" em vez de "total_size_bytes" [EXCLUIR DEPOIS DO MARCO 3 SER INTEGRADO]
                    chunk_count=distribuicao.get(
                        "chunk_count", len(info.get("chunks", []))
                    ),
                    # `nodes_used` é repeated string → passa-se uma lista Python.
                    nodes_used=distribuicao.get("nodes_used", []),
                )
            )

        # 4) Empacota tudo no ListFilesResponse (`files` é repeated).
        return dfs_pb2.ListFilesResponse(files=entradas)

    def RegisterNode(self, request, context):
        """
        Registra um nó no cluster, pois quando um nó liga, ele precisa se apresentar ao coordenador para entrar na membership canônica e o coordenador saber que ele existe no cluster.

        Chamado UMA vez por cada nó, quando ele liga.
        O nó se apresenta informando seu node_id, host:port onde escuta gRPC, e quanto espaço livre tem em disco.

        O coordenador armazena isso via NodeRegistry.register_node() que também marca este momento como o "primeiro batimento",
        para o nó já entrar como ALIVE imediatamente após registrar.

        Retornamos parâmetros do cluster (REPLICATION_FACTOR, CHUNK_SIZE, intervalo de heartbeat) para que TODOS os nós usem os mesmos valores,
        o que evita inconsistências, como um nó estabelecer localmente um intervalo de heartbeat diferente do config.py.
        """
        # Lógica do registry.
        self.registry.register_node(
            node_id=request.node.node_id,
            host=request.node.host,
            port=request.node.port,
            free_space_bytes=request.free_space_bytes,
        )

        # Resposta de sucesso, carregando os parâmetros do cluster para o nó armazenar localmente.
        return dfs_pb2.RegisterNodeResponse(
            ok=True,
            message=f"Nó {request.node.node_id} registrado",
            cluster_node_count=self.registry.size(),
            replication_factor=REPLICATION_FACTOR,
            chunk_size_bytes=CHUNK_SIZE,
            heartbeat_interval_secs=HEARTBEAT_INTERVAL,
        )

    def Heartbeat(self, request, context):
        """
        Recebe o batimento periódico de um nó (a cada ~2s, conforme HEARTBEAT_INTERVAL).

        Enquanto o RegisterNode é o registro inicial de que o nó ligou (uma vez só), o Heartbeat é o status de vida (repetido o tempo todo).
        É a AUSÊNCIA de batimentos que o coordenador usa, mais tarde, para classificar um nó como SUSPECT ou DEAD, e esse cálculo é feito sob demanda em NodeRegistry.status_of.
        Esta RPC só GRAVA o sinal de vida; a CLASSIFICAÇÃO é uma leitura separada.

        O batimento também carrega o estado fresco do nó:
          - free_space_bytes / active_uploads / active_downloads:
          Reservados para escolher ingress/egress por carga (refinamento do round-robin);
          - chunk_ids: o "block report" (inventário de chunks que o nó possui).
          No Marco 3 apenas guardamos, no Marco 4 será utilizado para achar chunks órfãos/perdidos e re-replicar.

        Esta RPC é só um ADAPTADOR: tira os campos do request, delega para NodeRegistry.record_heartbeat, e empacota a resposta.
        Toda a regra de armazenamento do estado vivo vive no registry.
        """
        # Delega para o registry. O retorno diz se o nó é CONHECIDO:
        # - True: batimento aceito; estado e last_heartbeat atualizados.
        # - False: nó desconhecido (nunca registrou E não está na membership canônica do config). O nó deve chamar RegisterNode antes.
        conhecido = self.registry.record_heartbeat(
            node_id=request.node_id,
            free_space_bytes=request.free_space_bytes,
            active_uploads=request.active_uploads,
            active_downloads=request.active_downloads,
            chunk_ids=request.chunk_ids,  # record_heartbeat já faz a cópia com list()
        )

        # chunks_to_delete fica VAZIO no Marco 3: detectar chunks órfãos (existem no disco do nó mas não nos metadados) e mandar apagá-los pertence ao marco de tolerância a falhas (Marco 4).
        # O campo já existe no contrato, então ligá-lo depois é só lógica no coordenador, não muda a interface .proto.
        return dfs_pb2.HeartbeatResponse(ok=conhecido, chunks_to_delete=[])

    def RequestUpload(self, request, context):
        """
        Etapa 1 do PUT: a CLI pede permissão para enviar um arquivo.

        O coordenador faz quatro coisas:
        1. Valida a requisição (caminho não vazio).
        2. Escolhe o ingress entre os nós VIVOS (alive_members + ingress_for_file).
        3. Pré-computa os ChunkPlacements com a membership canônica (replicas_for_chunk).
        4. Registra o upload pendente e responde com upload_id + ingress + placements.

        Por que ingress usa alive_members e placement usa canonical_members:
        - Ingress: precisa de um nó que está no ar AGORA para receber o stream de bytes.
        - Placement: o round-robin deve percorrer a lista canônica completa e estável.
            Um nó temporariamente como SUSPECT ou DEAD ainda é destino válido de placement.
            Quando voltar, a re-replicação (Marco 4) entrega os chunks que ficaram devendo.
        """
        caminho_logico = request.logical_path.strip()

        # 1. Validação
        if not caminho_logico:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("logical_path não pode ser vazio.")
            return dfs_pb2.RequestUploadResponse(ok=False, message="Caminho vazio.")

        # 2. Escolher o ingress entre os nós vivos
        nos_vivos = self.registry.alive_members()

        if not nos_vivos:
            # Nenhum nó está vivo: não há para onde enviar o arquivo.
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("Nenhum nó disponível no cluster.")
            return dfs_pb2.RequestUploadResponse(
                ok=False, message="Cluster sem nós vivos."
            )

        # O contador de arquivos é o próprio número de uploads já registrados.
        # Funciona como índice para o round-robin do ingress_for_file: cada arquivo novo rotaciona para o próximo nó da lista.
        with self._lock_uploads:
            indice_arquivo = len(self._uploads_pendentes)

        no_ingress = placement.ingress_for_file(
            file_index=indice_arquivo,
            nodes=nos_vivos,
            cluster_size=len(nos_vivos),
        )

        # 3. Pré-computar os ChunkPlacements com a membership canônica
        nos_canonicos = self.registry.canonical_members()
        tamanho_cluster = self.registry.size()

        # Quantos chunks este arquivo vai gerar.
        # math.ceil garante que o último pedaço (possivelmente menor que CHUNK_SIZE) também vire um chunk
        # Sem ceil, um arquivo de exatamente N*CHUNK_SIZE bytes seria o único caso correto. Qualquer outro perderia o último fragmento.
        total_chunks = max(1, math.ceil(request.total_size_bytes / CHUNK_SIZE))

        # Identificador único deste upload. Usado para amarrar as três mensagens:
        # RequestUpload, UploadFile (stream), ConfirmUpload.
        # Garante que mesmo uploads do mesmo arquivo (mesmo caminho) geram IDs diferentes, evitando colisão de nomes em disco.
        upload_id = str(uuid.uuid4())  # gera um UUID aleatório e converte para string

        placements = []
        for chunk_index in range(total_chunks):
            # Tamanho efetivo deste chunk.
            # Os chunks têm tamanho CHUNK_SIZE. O último pode ser menor (resto da divisão).
            bytes_antes = (
                chunk_index * CHUNK_SIZE
            )  # quantos bytes já foram alocados para os chunks anteriores
            tamanho_chunk = min(CHUNK_SIZE, request.total_size_bytes - bytes_antes)

            # ID estável do chunk: combina upload_id + índice ordinal.
            # Estável = o mesmo arquivo enviado duas vezes gera IDs diferentes
            # (porque upload_id é UUID diferente), o que evita colisão de nomes em disco sem precisar de hash de conteúdo.
            chunk_id = f"{upload_id}_chunk_{chunk_index}"

            # replicas_for_chunk usa a membership CANÔNICA (todos os nós, vivos ou não).
            # cluster_size passado explicitamente para a função validar que não estamos
            # passando acidentalmente só os nós vivos (a blindagem do placement.py).
            replicas_nos = placement.replicas_for_chunk(
                chunk_index=chunk_index,
                nodes=nos_canonicos,
                replication_factor=REPLICATION_FACTOR,
                cluster_size=tamanho_cluster,
            )

            # Converte NodeInfo para NodeRef (mensagem do .proto).
            replicas_proto = [
                dfs_pb2.NodeRef(node_id=no.node_id, host=no.host, port=no.port)
                for no in replicas_nos
            ]

            placements.append(
                dfs_pb2.ChunkPlacement(
                    chunk_id=chunk_id,
                    chunk_index=chunk_index,
                    size_bytes=tamanho_chunk,
                    replicas=replicas_proto,
                )
            )

        # 4. Registrar upload pendente e responder
        ingress_proto = dfs_pb2.NodeRef(
            node_id=no_ingress.node_id,
            host=no_ingress.host,
            port=no_ingress.port,
        )

        # Protege o acesso ao dicionário de uploads pendentes, garantindo que uploads simultâneos não causem condições de corrida.
        with self._lock_uploads:
            self._uploads_pendentes[upload_id] = {
                "logical_path": caminho_logico,
                "total_size_bytes": request.total_size_bytes,
                "ingress_node_id": no_ingress.node_id,
                "chunks": placements,  # guardamos os ChunkPlacement já prontos
            }

        print(
            f"[RequestUpload] path={caminho_logico} | "
            f"upload_id={upload_id} | "
            f"ingress={no_ingress.node_id} | "
            f"{total_chunks} chunk(s)"
        )

        return dfs_pb2.RequestUploadResponse(
            ok=True,
            message="Upload autorizado.",
            upload_id=upload_id,
            ingress=ingress_proto,
            chunks=placements,
        )

    def ConfirmUpload(self, request, context):
        """
        Etapa final do PUT: o INGRESS confirma que o upload terminou.
        O ingress confirma porque ele é quem orquestrou a replicação, então é ele quem SABE em quais nós cada chunk foi de fato gravado.
        Confirmar a partir do ingress também tira essa responsabilidade de um cliente fraco.

        É só AQUI que o arquivo passa a existir para o sistema: antes do ConfirmUpload, o upload era apenas PENDENTE
        (planejado no RequestUpload, guardado em _uploads_pendentes, mas não gravados).
        Depois daqui, está nos metadados e um GET no caminho lógico vai encontrá-lo.

        Quatro passos:
        1. Valida o upload_id contra _uploads_pendentes.
        2. Converte os ChunkPlacement (protobuf) para dicionários simples.
        3. Grava nos metadados no formato via metadata_service.put_file.
        4. Remove o upload da fila de pendentes e responde Ack.
        """
        upload_id = request.upload_id

        # 1. Validar o upload_id
        # Tiramos o upload pendente da fila aqui (com pop), dentro do lock.
        # pop com default None: se o id não existe, devolve None em vez de estourar.
        # Fazer pop (em vez de só ler) garante que um ConfirmUpload repetido com o mesmo id não grave o arquivo duas vezes.
        with self._lock_uploads:
            pendente = self._uploads_pendentes.pop(upload_id, None)

        if pendente is None:
            # upload_id desconhecido: ou nunca foi autorizado ou já foi confirmado antes.
            # O coordenador não grava nos metadados algo que não planejou.
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"upload_id desconhecido ou já confirmado: {upload_id}")
            return dfs_pb2.Ack(ok=False, message="Upload pendente não encontrado.")

        # 2. Converter os ChunkPlacement (protobuf) para dicionários.
        # O ingress reporta o que EFETIVAMENTE gravou.
        # Convertemos cada ChunkPlacement do .proto para um dict simples, para o MetadataService não precisar conhecer tipos do protobuf (mantém a camada desacoplada do gRPC).
        # Guardamos os node_id das réplicas como lista de strings.
        chunks_para_gravar = []
        for chunk in request.chunks:
            chunks_para_gravar.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "chunk_index": chunk.chunk_index,
                    "size_bytes": chunk.size_bytes,
                    # replicas no .proto é uma lista de NodeRef; guardamos só os ids.
                    "replicas": [r.node_id for r in chunk.replicas],
                }
            )

        # 3. Grava os metadados no formato via metadata_service.put_file.
        # Usamos o logical_path que guardamos no RequestUpload (não um vindo do ingress), poisé o que o coordenador autorizou.
        caminho_logico = pendente["logical_path"]

        self.metadata.put_file(
            path=caminho_logico,
            total_size_bytes=request.total_size_bytes,
            chunks=chunks_para_gravar,
        )

        # Relê o arquivo recém-gravado só para recuperar o timestamp e exibir no log.
        # Isso evidencia, na demonstração, que o controle de timestamp está ativo.
        info_gravada = self.metadata.get_file(caminho_logico)
        carimbo = info_gravada.get("uploaded_at", "—") if info_gravada else "—"

        # 4. Responder (o upload já saiu da fila no passo 1)
        print(
            f"[ConfirmUpload] path={caminho_logico} | "
            f"upload_id={upload_id} | "
            f"{len(chunks_para_gravar)} chunk(s) gravado(s) nos metadados | "
            f"uploaded_at={carimbo}"
        )

        return dfs_pb2.Ack(
            ok=True,
            message=f"Upload {upload_id} confirmado e gravado nos metadados.",
        )

    def RequestDownload(self, request, context):
        """
        Etapa 1 do GET: a CLI pede para baixar um arquivo.

        Espelha o RequestUpload, mas com uma diferença central: aqui o coordenador NÃO calcula placement nenhum.
        O placement já foi decidido no upload e está gravado nos metadados. O RequestDownload apenas LÊ.

        Placement não é recalculado porque se a quantidade canônica de nós mudar desde o upload, o round-robin daria um resultado diferente do que está REALMENTE no disco.
        Os metadados são a única verdade sobre onde cada chunk está. Essa regra é o que viabiliza a elasticidade.

        Três passos:
        1. Lê os metadados do arquivo (404 se não existe).
        2. Escolhe o egress por LOCALIDADE: o nó VIVO com mais chunks do arquivo.
            Menos chunks faltando localmente = menos buscas em peers (FetchChunk) = download mais rápido.
        3. Responde com download_id, egress, tamanho total e o mapa de chunks.
        """
        caminho_logico = request.logical_path.strip()

        # 1. Ler os metadados do arquivo
        info = self.metadata.get_file(caminho_logico)
        if info is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Arquivo não encontrado: {caminho_logico}")
            return dfs_pb2.RequestDownloadResponse(
                ok=False, message="Arquivo não encontrado."
            )

        chunks_meta = info.get("chunks", [])

        # 2. Escolher o egress por localidade, entre os nós VIVOS
        # Conjunto de ids dos nós vivos agora: só eles podem servir bytes.
        nos_vivos = self.registry.alive_members()
        ids_vivos = {no.node_id for no in nos_vivos}

        if not ids_vivos:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("Nenhum nó disponível no cluster.")
            return dfs_pb2.RequestDownloadResponse(
                ok=False, message="Cluster sem nós vivos."
            )

        # Conta, para cada nó vivo, quantos chunks deste arquivo ele guarda.
        # Percorremos os chunks dos metadados e somamos 1 ao contador do nó, para cada réplica que esteja viva.
        # O nó com a maior contagem é o que precisará buscar menos chunks em peers, então é o melhor egress.
        contagem_por_no: dict[str, int] = {}
        for chunk in chunks_meta:
            for node_id in chunk["replicas"]:
                if node_id in ids_vivos:
                    contagem_por_no[node_id] = contagem_por_no.get(node_id, 0) + 1

        if not contagem_por_no:
            # Nenhum nó vivo tem NENHUM chunk deste arquivo.
            # No Marco 3, isso significa indisponibilidade temporária. Respondemos erro claro em vez de um egress inútil.
            # A recuperação (re-replicação) ficará para o Marco 4.
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("Nenhuma réplica viva para este arquivo no momento.")
            return dfs_pb2.RequestDownloadResponse(
                ok=False, message="Sem réplicas vivas para o arquivo."
            )

        # Escolhe o nó com mais chunks locais.
        # max com chave dupla: primeiro pela contagem (queremos a MAIOR),
        # depois pelo node_id invertido para desempate (menor id ganha).
        # Desempate por carga (active_downloads) fica como refinamento futuro, carga instantânea é métrica fraca neste cenário, já que o nó pode estar no meio de um download pesado ou leve, e o que importa é a quantidade de chunks locais para evitar fetch em peers.
        egress_node_id = max(
            contagem_por_no,
            key=lambda nid: (contagem_por_no[nid], -self.registry.index_of(nid)),
        )

        # Pega o NodeInfo do egress escolhido (para ter host:port).
        egress_info = self.registry.get(egress_node_id)
        egress_proto = dfs_pb2.NodeRef(
            node_id=egress_info.node_id,
            host=egress_info.host,
            port=egress_info.port,
        )

        # 3. Montar a resposta: download_id + egress + mapa de chunks
        # Reconstruímos os ChunkPlacement a partir dos metadados gravados.
        # (NÃO recalculamos placement, só convertemos o que está salvo de volta para o formato .proto que a CLI/egress esperam.)
        placements = []
        for chunk in chunks_meta:
            replicas_proto = [
                # Para cada node_id salvo, buscamos o endereço atual na canônica.
                dfs_pb2.NodeRef(
                    node_id=node_id,
                    host=self.registry.get(node_id).host,
                    port=self.registry.get(node_id).port,
                )
                for node_id in chunk["replicas"]
            ]
            placements.append(
                dfs_pb2.ChunkPlacement(
                    chunk_id=chunk["chunk_id"],
                    chunk_index=chunk["chunk_index"],
                    size_bytes=chunk["size_bytes"],
                    replicas=replicas_proto,
                )
            )

        # download_id: identifica esta operação de download. Análogo ao upload_id.
        # A CLI passa este token ao egress no DownloadFile e o egress valida.
        download_id = str(uuid.uuid4())

        # total_size_bytes: lido dos metadados
        total_size = info.get("total_size_bytes")

        print(
            f"[RequestDownload] path={caminho_logico} | "
            f"download_id={download_id} | "
            f"egress={egress_node_id} ({contagem_por_no[egress_node_id]} de "
            f"{len(chunks_meta)} chunks locais)"
        )

        return dfs_pb2.RequestDownloadResponse(
            ok=True,
            message="Download autorizado.",
            download_id=download_id,
            egress=egress_proto,
            total_size_bytes=total_size,
            chunks=placements,
        )

    def DeleteFile(self, request, context):
        """
        Apaga um arquivo: os chunks físicos (comandando os nós) e os metadados.

        Chunks primeiro, metadados depois:
        Se apagássemos os metadados primeiro e o processo morresse no meio, os chunks ficariam órfãos no disco sem registro de que existem (lixo invisível que ninguém limparia).
        No sentido inverso, o pior caso é metadado apontando para chunk já apagado, o que o GET detecta. Preferimos o erro detectável ao silencioso.

        PARALELISMO: um nó por thread, um canal por nó:
        Em vez de percorrer chunk a chunk em série (lento: milhares de chamadas esperando uma à outra num arquivo grande), invertemos o mapa para "nó -> chunks que ele guarda" e disparamos um nó por thread.
        Cada thread abre UM canal para o seu nó e apaga todos os chunks daquele nó por ele.

        Réplica morta agora não pode apagar seu chunk; contamos como falha e seguimos.
        O órfão é limpo quando o nó voltar (Marco 4, chunks_to_delete).
        """
        caminho_logico = request.logical_path.strip()

        # 1. Ler os metadados (fonte de onde estão os chunks)
        info = self.metadata.get_file(caminho_logico)
        if info is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Arquivo não encontrado: {caminho_logico}")
            return dfs_pb2.Ack(ok=False, message="Arquivo não encontrado.")

        chunks_meta = info.get("chunks", [])

        # 2. Inverter o mapa: de "chunk -> réplicas" para "nó -> seus chunks"
        # Os metadados guardam, por chunk, a lista de nós que o têm.
        # Para apagar com um canal por nó, precisamos do agrupamento oposto: para cada nó, a lista de chunks que ele guarda.
        # Montamos isso percorrendo os chunks uma vez e acumulando em um dicionário {node_id: [chunk_id, ...]}.
        chunks_por_no: dict[str, list[str]] = {}
        for chunk in chunks_meta:
            chunk_id = chunk["chunk_id"]
            for node_id in chunk["replicas"]:
                chunks_por_no.setdefault(node_id, []).append(chunk_id)

        # 3. Disparar a deleção em paralelo: um nó por thread, um canal por nó
        # Usamos um ThreadPoolExecutor: cada nó vira uma tarefa que chama delete_node_chunks (que reusa um único canal para todos os chunks daquele nó).
        # As tarefas rodam concorrentemente: os nós apagam ao mesmo tempo, em vez de um esperar o outro.
        total_ok = 0
        total_falhas = 0

        # max_workers limitado ao número de nós envolvidos: não adianta ter mais threads que nós.
        # max(1, ...) evita pool de tamanho zero se o arquivo (por algum motivo) não tiver réplicas registradas.
        with futures.ThreadPoolExecutor(
            max_workers=max(1, len(chunks_por_no))
        ) as executor:
            tarefas = {}
            for node_id, ids_dos_chunks in chunks_por_no.items():
                # Resolve o endereço atual do nó na membership canônica.
                try:
                    no = self.registry.get(node_id)
                except KeyError:
                    # node_id que não está mais na canônica (cenário raro):
                    # conta os chunks dele como falha e não cria tarefa.
                    total_falhas += len(ids_dos_chunks)
                    continue

                # Agenda a deleção dos chunks deste nó.
                # A tarefa devolverá (sucessos, falhas). Guardamos o node_id para o log.
                futuro = executor.submit(
                    replication_client.delete_node_chunks,
                    no.host,
                    no.port,
                    ids_dos_chunks,
                )
                tarefas[futuro] = node_id

            # Conforme cada tarefa termina, somamos seus resultados ao total.
            # as_completed devolve os futuros na ordem em que TERMINAM (não na de submissão), o que é exatamente o que queremos: agregamos quem acabar primeiro.
            for futuro in futures.as_completed(tarefas):
                node_id = tarefas[futuro]
                ok, falhas = futuro.result()
                total_ok += ok
                total_falhas += falhas
                print(f"[DeleteFile] nó {node_id}: {ok} apagado(s), {falhas} falha(s)")

        # 4. Remover os metadados e responder
        # Removemos mesmo que algumas deleções físicas tenham falhado: o arquivo deixa de existir logicamente.
        # Os chunks remanescentes em nós mortos viram órfãos a serem limpos no Marco 4.
        # Manter os metadados aqui daria a impressão falsa de que o arquivo ainda está íntegro.
        self.metadata.delete_file(caminho_logico)

        print(
            f"[DeleteFile] path={caminho_logico} | "
            f"{total_ok} chunk(s)-réplica apagado(s), {total_falhas} falha(s) | "
            f"metadados removidos"
        )

        return dfs_pb2.Ack(
            ok=True,
            message=(
                f"Arquivo '{caminho_logico}' removido. "
                f"{total_ok} réplicas apagadas, {total_falhas} falhas (best-effort)."
            ),
        )


def main():
    """Sobe o coordenador via gRPC expondo o ControlService."""
    # Pool de 50 threads: o gRPC despacha cada chamada numa thread daqui,
    # atendendo vários clientes em paralelo.
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=50))

    # Instâncias COMPARTILHADAS: o servicer e o watcher precisam ver o mesmo registry (estado vivo dos nós) e o mesmo metadata (mapa de chunks).
    # Sem compartilhar, o watcher veria um índice/registro diferentes.
    metadata = MetadataService()
    registry = NodeRegistry()

    servicer = ControlServiceServicer(metadata=metadata, registry=registry)

    # Registra o ControlService no servidor.
    dfs_pb2_grpc.add_ControlServiceServicer_to_server(servicer, server)

    address = f"{COORDINATOR_HOST}:{COORDINATOR_PORT}"
    server.add_insecure_port(address)

    print(f"🚀 Coordenador DFS ouvindo via gRPC em {address}")
    print("   Serviço registrado: ControlService")

    server.start()
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("\nCoordenador encerrado pelo usuário.")
        server.stop(0)


if __name__ == "__main__":
    main()
