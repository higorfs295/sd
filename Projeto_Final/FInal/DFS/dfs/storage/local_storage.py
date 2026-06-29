# dfs/storage/local_storage.py
"""
Camada de persistência local de um storage node.

Mantém a API legada (put/get/delete/list_files por caminho lógico) e adiciona a
API orientada a chunk_id usada pelo plano de dados do Marco 3.

CONVENÇÃO DE NOME DO CHUNK (importante p/ o observer):
  O chunk_id vem no formato "<upload_id>_chunk_<indice>" (sugestão do .proto).
  Gravamos o arquivo do chunk com ESSE nome, SEM extensão, em
  <root>/chunks/ <chunk_id>. Assim o nome termina em "_chunk_<N>" e casa com o
  regex do observer (_chunk_\\d+$). Se algum dia quiser usar extensão (.bin),
  ajuste o REGEX_CHUNK do observer junto.
"""
from __future__ import annotations

from pathlib import Path

from dfs.config import STORAGE_DIR

CHUNKS_SUBDIR = "chunks"


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
        """Grava um chunk inteiro no disco local. Retorna bytes gravados."""
        target = self._chunk_path(chunk_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return len(data)

    def read_chunk(self, chunk_id: str) -> bytes:
        """Lê um chunk inteiro. Levanta FileNotFoundError se não existir."""
        return self._chunk_path(chunk_id).read_bytes()

    def has_chunk(self, chunk_id: str) -> bool:
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
        return sorted(p.name for p in base.iterdir() if p.is_file())
