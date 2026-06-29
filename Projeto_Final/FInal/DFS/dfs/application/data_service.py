# dfs/application/data_service.py
"""
DataServicer — interface da CLI com o nó (plano de dados, streaming).
  UploadFile  : o nó é o INGRESS (recebe o arquivo, fatia em chunks oficiais,
                replica em paralelo e confirma ao coordenador).
  DownloadFile: o nó é o EGRESS (junta chunks locais + busca em peers e
                devolve em ordem).

O mapa de ChunkPlacement vem do PlanStore (handoff via DataPlaneService.SetUploadPlan
/ SetDownloadPlan), resolvido por upload_id / download_id.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import grpc

from dfs.config import STREAM_SIZE, REPLICATION_FACTOR
from dfs.cluster.control_client import ControlClient
from dfs.cluster.replication_client import ReplicationClient
from dfs.pb import dfs_pb2, dfs_pb2_grpc


class DataServicer(dfs_pb2_grpc.DataServiceServicer):
    def __init__(self, storage, node_id: str, plans, control_factory=ControlClient):
        self.storage = storage
        self.node_id = node_id
        self.plans = plans  # PlanStore compartilhado com o DataPlaneServicer
        self._control_factory = control_factory  # injetável para teste

    # ------------------------------------------------------------------- PUT
    def UploadFile(self, request_iterator, context):
        upload_id = None
        chunk_index = 0
        chunks_written = 0
        total_bytes = 0
        buffer = bytearray()
        confirmados = []
        plano_por_indice = {}  # chunk_index -> [NodeRef]
        tamanho_por_indice = {}  # chunk_index -> size_bytes (chunk adaptável)

        def carregar_plano():
            entrada = self.plans.get_upload(upload_id)
            if entrada is None:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    f"sem plano para upload_id={upload_id}; "
                    "chame SetUploadPlan antes de UploadFile",
                )
            _total, chunks = entrada
            for cp in chunks:
                plano_por_indice[cp.chunk_index] = list(cp.replicas)
                tamanho_por_indice[cp.chunk_index] = (
                    cp.size_bytes
                )  # tamanho deste chunk

        def replicas_do(idx):
            reps = plano_por_indice.get(idx)
            if reps is None:
                context.abort(
                    grpc.StatusCode.OUT_OF_RANGE,
                    f"chunk {idx} fora do plano (descasamento de tamanho?)",
                )
            return reps

        def fechar_chunk(idx, data):
            nonlocal chunks_written
            chunk_id = f"{upload_id}_chunk_{idx}"
            reps = replicas_do(idx)
            # grava local se este nó é uma das réplicas
            if any(r.node_id == self.node_id for r in reps):
                self.storage.store_chunk(chunk_id, data)
            # fan-out para as demais réplicas, em paralelo
            self._fan_out(chunk_id, idx, upload_id, data, reps)
            confirmados.append(
                dfs_pb2.ChunkPlacement(
                    chunk_id=chunk_id,
                    chunk_index=idx,
                    size_bytes=len(data),
                    replicas=reps,
                )
            )
            chunks_written += 1

        for msg in request_iterator:
            if upload_id is None and msg.upload_id:
                upload_id = msg.upload_id
                carregar_plano()
            if msg.data:
                buffer.extend(msg.data)
                total_bytes += len(msg.data)

                # Fatia usando o tamanho planejado para este chunk (chunk adaptável).
                # O tamanho vem do plano (size_bytes de cada ChunkPlacement).
                tam = tamanho_por_indice.get(chunk_index)
                while tam is not None and len(buffer) >= tam:
                    fechar_chunk(chunk_index, bytes(buffer[:tam]))
                    del buffer[:tam]
                    chunk_index += 1
                    tam = tamanho_por_indice.get(chunk_index)
        if upload_id is None:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "upload sem upload_id")
        if (
            buffer or chunks_written == 0
        ):  # último chunk (resto), inclusive arquivo vazio
            fechar_chunk(chunk_index, bytes(buffer))

        # confirma ao coordenador o que foi gravado e libera o plano
        try:
            ctrl = self._control_factory()
            ctrl.confirm_upload(upload_id, confirmados, total_bytes)
            ctrl.close()
        except Exception as exc:  # noqa: BLE001
            return dfs_pb2.UploadResult(
                ok=False,
                message=f"ConfirmUpload falhou: {exc}",
                chunks_written=chunks_written,
                total_bytes_written=total_bytes,
            )
        finally:
            self.plans.clear_upload(upload_id)

        return dfs_pb2.UploadResult(
            ok=True,
            message="upload concluído",
            chunks_written=chunks_written,
            total_bytes_written=total_bytes,
        )

    def _fan_out(self, chunk_id, idx, upload_id, data, reps):
        alvos = [r for r in reps if r.node_id != self.node_id]

        def enviar(r):
            # Tentar gravar numa réplica.
            # Se ela estiver morta/inacessível, não propaga a exceção: devolve None (= "não confirmou"), e a política de quórum W decide se ainda há réplicas vivas suficientes.
            cli = ReplicationClient(r.host, r.port)
            try:
                return cli.store_chunk(chunk_id, idx, upload_id, self.node_id, data)
            except Exception as e:  # noqa: BLE001
                print(f"[{self.node_id}] réplica {r.node_id} indisponível no fan-out.")
                return None
            finally:
                cli.close()

        if not alvos:
            return
        with ThreadPoolExecutor(max_workers=len(alvos)) as ex:
            resultados = list(ex.map(enviar, alvos))
        # Política de quórum W: conta réplicas confirmadas (local + remotas ok).
        ok_remotas = sum(1 for r in resultados if getattr(r, "ok", False))
        sou_replica = any(r.node_id == self.node_id for r in reps)
        confirmadas = ok_remotas + (1 if sou_replica else 0)
        w = min(2, len(reps))  # W=2 (ou nº de réplicas, se menor)
        if confirmadas < w:
            raise RuntimeError(
                f"quórum de escrita não atingido p/ {chunk_id}: "
                f"{confirmadas}/{w} réplicas"
            )

    # ------------------------------------------------------------------- GET
    def DownloadFile(self, request, context):
        download_id = request.download_id
        entrada = self.plans.get_download(download_id)
        if entrada is None:
            context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                f"sem plano para download_id={download_id}; "
                "chame SetDownloadPlan antes de DownloadFile",
            )
        _total, chunks = entrada
        chunks = sorted(chunks, key=lambda c: c.chunk_index)
        try:
            for cp in chunks:
                if self.storage.has_chunk(cp.chunk_id):
                    data = self.storage.read_chunk(cp.chunk_id)
                else:
                    data = self._buscar_em_peer(cp)
                emitido = False
                for i in range(0, len(data), STREAM_SIZE):
                    ultimo = cp.chunk_index == chunks[
                        -1
                    ].chunk_index and i + STREAM_SIZE >= len(data)
                    yield dfs_pb2.DownloadChunk(
                        data=data[i : i + STREAM_SIZE], is_last=ultimo
                    )
                    emitido = True
                if not emitido:  # chunk vazio
                    ultimo = cp.chunk_index == chunks[-1].chunk_index
                    yield dfs_pb2.DownloadChunk(data=b"", is_last=ultimo)
        finally:
            self.plans.clear_download(download_id)

    def _buscar_em_peer(self, cp) -> bytes:
        for r in cp.replicas:
            if r.node_id == self.node_id:
                continue
            cli = ReplicationClient(r.host, r.port)
            try:
                return cli.fetch_chunk(cp.chunk_id, self.node_id)
            except Exception:  # noqa: BLE001
                continue
            finally:
                cli.close()
        raise RuntimeError(f"nenhuma réplica viva para {cp.chunk_id}")
