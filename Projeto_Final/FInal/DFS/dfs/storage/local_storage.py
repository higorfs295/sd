# dfs/storage/local_storage.py
"""
Camada de persistência local de um storage node.

Mantém a API legada (put/get/delete/list_files por caminho lógico) e adiciona a
API orientada a chunk_id usada pelo plano de dados do Marco 3.

CONVENÇÃO DE NOME DO CHUNK (importante p/ o observer):
  O chunk_id vem no formato "<upload_id>_chunk_<indice>" (sugestão do .proto).
  Gravamos o arquivo do chunk com ESSE nome, SEM extensão, em
  <root>/chunks/<chunk_id>. Assim o nome termina em "_chunk_<N>" e casa com o
  regex do observer (_chunk_\\d+$). Se algum dia quiser usar extensão (.bin),
  ajuste o REGEX_CHUNK do observer junto.

ESCRITA ATÔMICA (correção de integridade):
  store_chunk grava primeiro em "<chunk_id>.tmp" e só então faz os.replace para o
  nome final. os.replace é atômico dentro do mesmo filesystem: ou o chunk final
  existe inteiro, ou nem aparece. Nunca há um chunk PARCIAL com o nome final.
  Consequências:
    - Uma escrita interrompida (processo morto no meio) deixa no máximo um ".tmp",
      nunca um chunk final corrompido. Esse ".tmp" é exatamente o que o
      storage_garbage_collector.py limpa (agora o ramo de ".tmp" faz sentido).
    - list_chunk_ids NUNCA reporta ".tmp": o block report não conta escritas em
      andamento como chunks válidos, evitando falso "órfão" e leituras de parcial.
"""
from __future__ import annotations

import os
from pathlib import Path

from dfs.config import STORAGE_DIR

CHUNKS_SUBDIR = "chunks"
TMP_SUFFIX = ".tmp"


class LocalStorage:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else STORAGE_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ legado
    def _resolve_path(self, path: str) -> Path:
        target = (self.root / path).resolve()
        root_resolved = self.root.resolve()
        if root_resolved not in target.parents and target != root_resolved:
            raise ValueError("Caminho inválido fora da raiz do storage")
        return target

    def put(self, path: str, data: bytes) -> None:
        target = self._resolve_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get(self, path: str) -> bytes:
        return self._resolve_path(path).read_bytes()

    def delete(self, logical_path: str) -> None:
        physical = self._resolve_path(logical_path)
        if physical.exists():
            physical.unlink()
        parent = physical.parent
        root_resolved = self.root.resolve()
        while parent != root_resolved and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def list_files(self) -> list[str]:
        return sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file()
        )

    # ------------------------------------------------------- API por chunk_id
    def _chunk_path(self, chunk_id: str) -> Path:
        safe = chunk_id.replace("/", "_").replace("\\", "_")
        if not safe:
            raise ValueError("chunk_id vazio")
        target = (self.root / CHUNKS_SUBDIR / safe).resolve()
        base = (self.root / CHUNKS_SUBDIR).resolve()
        if base not in target.parents and target != base:
            raise ValueError("chunk_id inválido (path traversal)")
        return target

    def store_chunk(self, chunk_id: str, data: bytes) -> int:
        """
        Grava um chunk inteiro no disco local de forma ATÔMICA. Retorna bytes gravados.

        Escreve em "<chunk_id>.tmp" e só então os.replace para o nome final. Se algo
        falhar no meio, remove o ".tmp" e propaga a exceção — nunca deixa um chunk
        final parcial. O ".tmp" residual (se o processo morrer antes do replace) é
        limpo pelo storage_garbage_collector.py.
        """
        target = self._chunk_path(chunk_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + TMP_SUFFIX)
        try:
            # Grava o conteúdo no temporário e força a descarga no disco antes do rename.
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())  # garante os bytes em disco antes do replace
            os.replace(tmp, target)  # atômico no mesmo filesystem
        except Exception:
            # Escrita interrompida: limpa o parcial e propaga (quem chama trata/loga).
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise
        return len(data)

    def read_chunk(self, chunk_id: str) -> bytes:
        """Lê um chunk inteiro. Levanta FileNotFoundError se não existir."""
        return self._chunk_path(chunk_id).read_bytes()

    def has_chunk(self, chunk_id: str) -> bool:
        # Só considera presente o chunk FINAL (um ".tmp" em andamento não conta).
        return self._chunk_path(chunk_id).exists()

    def delete_chunk(self, chunk_id: str) -> bool:
        target = self._chunk_path(chunk_id)
        if target.exists():
            target.unlink()
            return True
        return False

    def list_chunk_ids(self) -> list[str]:
        base = self.root / CHUNKS_SUBDIR
        if not base.exists():
            return []
        # Exclui ".tmp": escritas em andamento não entram no block report do heartbeat,
        # evitando falso "órfão" e evitando anunciar um chunk que ainda não existe inteiro.
        return sorted(
            p.name
            for p in base.iterdir()
            if p.is_file() and not p.name.endswith(TMP_SUFFIX)
        )
