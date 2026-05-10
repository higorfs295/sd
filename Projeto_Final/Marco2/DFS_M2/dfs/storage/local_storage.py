"""
DESCRIÇÃO GERAL:
Camada de persistência local utilizada pelos storage nodes.

Cada nó possui:
- um diretório raiz próprio;
- arquivos/chunks físicos;
- diretórios físicos internos.

Esta classe abstrai:
- leitura;
- escrita;
- remoção;
- criação de diretórios;
- remoção de diretórios.

O coordenador NÃO manipula disco diretamente.
Tudo passa por esta camada.
"""

from pathlib import Path

from dfs.config import STORAGE_DIR


class LocalStorage:
    """
    Implementa o armazenamento local físico de um nó.
    """

    def __init__(self, root: Path | None = None):
        """
        Inicializa o storage local.
        """

        self.root = Path(root) if root is not None else STORAGE_DIR

        # Garante existência da raiz física do nó.
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, path: str) -> Path:
        """
        Resolve caminhos relativos de forma segura.

        Também protege contra path traversal simples.
        """

        target = (self.root / path).resolve()

        root_resolved = self.root.resolve()

        if root_resolved not in target.parents and target != root_resolved:
            raise ValueError("Caminho inválido fora da raiz do storage")

        return target

    # ============================================================
    # ARQUIVOS
    # ============================================================

    def put(self, path: str, data: bytes) -> None:
        """
        Salva um arquivo físico no nó.
        """

        target = self._resolve_path(path)

        target.parent.mkdir(parents=True, exist_ok=True)

        target.write_bytes(data)

    def get(self, path: str) -> bytes:
        """
        Recupera um arquivo físico.
        """

        target = self._resolve_path(path)

        return target.read_bytes()

    def delete(self, logical_path: str) -> None:
        """
        Remove o chunk e limpa as pastas vazias (.chunks/nome_do_arquivo/)
        """
        physical_path = self._resolve_path(logical_path)

        # 1. Remove o arquivo do chunk
        if physical_path.exists():
            physical_path.unlink()

        # 2. Primeira tentativa: Remove a pasta do arquivo (ex: .chunks/meu_video.mp4/)
        try:
            parent_dir = physical_path.parent
            if parent_dir.exists():
                parent_dir.rmdir()
                
                # 3. Segunda tentativa: Remove a pasta .chunks/ se ela ficou vazia
                # Só chegamos aqui se a pasta do arquivo foi removida com sucesso
                grandparent_dir = parent_dir.parent
                if grandparent_dir.name == ".chunks" and grandparent_dir.exists():
                    grandparent_dir.rmdir()
        except OSError:
            # Se qualquer uma das pastas ainda tiver conteúdo (outros arquivos ou chunks),
            # o Python ignora e interrompe a limpeza automática naquele nível.
            pass
    # ============================================================
    # ============================================================
    # LISTAGEM
    # ============================================================

    def list_files(self) -> list[str]:
        """
        Lista todos os arquivos físicos armazenados.
        """

        return sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file()
        )