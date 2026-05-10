import json  # Usada para salvar e carregar o índice dos metadados
import threading
from pathlib import Path
from dfs.config import (
    METADATA_FILE,
)  # Caminho do arquivo onde os metadados serão salvos


# Define a classe responsável pelos metadados do DFS
# Ela funciona como um pequeno serviço de índice
class MetadataService:
    """
    Serviço responsável pela indexação do DFS

    O índice persistido tem o seguinte formato:
        {
            "files": {
                "<caminho_logico>": {
                    "path": "...",
                    "size": ...,
                    "chunks": [...],
                    "distribution": {...}
                }
            },
            "directories": {
                "<caminho_logico>": {
                    "path": "...",
                    "node_id": "...",
                    "shard_id": ...,
                    "fallback_used": ...
                }
            }
        }
    """

    def __init__(self):
        # Define o caminho do arquivo de metadados, garantindo que a pasta exista
        self.metadata_file = METADATA_FILE
        self.metadata_file.parent.mkdir(parents=True, exist_ok=True)

        # Cria um lock para proteger o índice contra acesso concorrente
        self._lock = threading.Lock()
        self._index = self._load()

    # Carrega o índice do arquivo de metadados JSON
    def _load(self) -> dict:
        # Estrutura padrão garantindo as duas sub-chaves sempre presentes.
        # Isso evita ter que checar existência em cada método.
        default = {"files": {}, "directories": {}}

        # Primeira execução: o arquivo de metadados não existe, então retorna um índice vazio
        if not self.metadata_file.exists():
            return default

        try:
            # Retorna o conteúdo do arquivo de metadados como um dicionário Python
            data = json.loads(self.metadata_file.read_text(encoding="utf-8"))
            # Garante compatibilidade caso o arquivo antigo não tenha as chaves.
            data.setdefault("files", {})
            data.setdefault("directories", {})
            return data
        except Exception:
            return default  # Se o arquivo estiver corrompido ou ilegível, retorna um índice vazio

    # Salva o índice atual em disco no formato JSON
    def _save(self) -> None:
        self.metadata_file.write_text(
            json.dumps(
                self._index, indent=4, ensure_ascii=False
            ),  # Salva o índice como JSON formatado para facilitar a leitura manual
            encoding="utf-8",
        )

    # ARQUIVOS

    # Registra ou atualiza um arquivo no índice
    # Chamado normalmente depois que o PUT salva todos os chunks nos nós
    def put_file(self, path: str, size: int, chunks: list[dict]) -> None:
        """
        Registra um arquivo no índice, dentro da seção 'files'
        Também calcula o resumo de distribuição entre os nós, usado pelo LIST
        """
        # Conta quantos chunks ficaram em cada nó. Útil pro list e pro relatório.
        nodes_used = sorted({c["node_id"] for c in chunks})

        with self._lock:
            self._index["files"][path] = (
                {  # Cria ou substitui a entrada do arquivo no índice
                    "path": path,
                    "size": size,
                    "chunks": chunks,  # Cada chunk é um dicionário com informações sobre onde o chunk está armazenado (chuck_id, node_id, shard_id, cunk_path, size)
                    "distribution": {
                        "chunk_count": len(chunks),
                        "nodes_used": nodes_used,
                    },
                }
            )
            self._save()

    # Busca as informações de um arquivo no índice
    # É usado principalmente no GET e no DELETE
    def get_file(self, path: str) -> dict | None:
        with self._lock:
            return self._index["files"].get(path)

    # Remove um arquivo do índice
    # Deve ser chamado depois que todos os chunks forem removidos dos nós
    def delete_file(self, path: str) -> None:
        with self._lock:
            self._index["files"].pop(path, None)
            self._save()

    # Verifica se um arquivo existe no índice
    def exists_file(self, path: str) -> bool:
        with self._lock:
            return path in self._index["files"]

    # Lista os arquivos conhecidos pelo índice
    def list_files(self) -> list[str]:
        with self._lock:
            # Retorna os caminhos lógicos dos arquivos indexados, ordenados alfabeticamente
            return sorted(self._index["files"].keys())

    # DIRETÓRIOS

    def put_directory(
        self,
        path: str,
        node_id: str,
        shard_id: int,
        fallback_used: bool = False,
    ) -> None:
        """
        Registra um diretório lógico no índice

        O FileService chama este método para criar um diretório lógico apontando para um shard específico do nó
        """
        with self._lock:
            self._index["directories"][path] = {
                "path": path,
                "node_id": node_id,
                "shard_id": shard_id,
                "fallback_used": fallback_used,
            }
            self._save()

    def exists_directory(self, path: str) -> bool:
        with self._lock:
            return path in self._index["directories"]

    # Verifica se um arquivo já existe no índice
    # Pode ser usado para evitar sobrescrita ou confirmar existência
    # def exists(self, path: str) -> bool:
    #     with self._lock:
    #         return path in self._index
    def list_directories(self) -> list[str]:
        """
        Lista todos os diretórios lógicos registrados.
        """

        with self._lock:
            return sorted(self._index["directories"].keys())

    def directory_is_empty(self, path: str) -> bool:
        """
        Verifica se um diretório lógico está vazio.

        Um diretório NÃO está vazio se:
        - possuir arquivos;
        - possuir subdiretórios.
        """

        normalized = path.rstrip("/")

        with self._lock:

            # Verifica arquivos
            for file_path in self._index["files"]:
                if file_path.startswith(f"{normalized}/"):
                    return False

            # Verifica subdiretórios
            for dir_path in self._index["directories"]:
                if dir_path == normalized:
                    continue

                if dir_path.startswith(f"{normalized}/"):
                    return False

        return True

    def delete_directory(self, path: str) -> None:
        """
        Remove um diretório lógico do metadata.
        """

        with self._lock:

            self._index["directories"].pop(path, None)
            self._save()

    # LISTAGEM UNIFICADA (usada pela CLI)

    def list_entries(self) -> list[str]:
        """
        Retorna uma visão unificada de arquivos e diretórios

        Esse formato é usado pela CLI no comando LIST
        """

        with self._lock:
            entries: list[str] = []

            # DIRETÓRIOS

            for path in sorted(self._index["directories"].keys()):
                info = self._index["directories"][path]
                node_id = info.get("node_id", "-")
                shard_id = info.get("shard_id", "-")
                entries.append(f"[DIR ] {path}  (node={node_id}, shard={shard_id})")

            # ARQUIVOS

            for path in sorted(self._index["files"].keys()):
                info = self._index["files"][path]
                chunks = info.get("chunks", [])
                distribution = info.get("distribution", {})
                nodes_used = distribution.get("nodes_used", [])

                entries.append(
                    f"[FILE] {path}  "
                    f"({len(chunks)} chunk(s), "
                    f"nodes={', '.join(nodes_used) if nodes_used else '-'})"
                )

            return entries
