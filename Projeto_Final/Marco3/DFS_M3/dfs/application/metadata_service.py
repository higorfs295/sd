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
        # Estrutura padrão garantindo a chave 'files' sempre presente.
        # Isso evita ter que checar existência em cada método.
        default = {"files": {}}

        # Primeira execução: o arquivo de metadados não existe, então retorna um índice vazio
        if not self.metadata_file.exists():
            return default

        try:
            # Retorna o conteúdo do arquivo de metadados como um dicionário Python
            data = json.loads(self.metadata_file.read_text(encoding="utf-8"))
            # Garante compatibilidade caso o arquivo antigo não tenha a chave.
            data.setdefault("files", {})
            
            # Limpeza caso exista resquício de 'directories' de versões anteriores no JSON salvo
            if "directories" in data:
                del data["directories"]

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

    # LISTAGEM UNIFICADA (usada pela CLI)

    def list_entries(self) -> list[str]:
        """
        Retorna uma visão unificada dos arquivos

        Esse formato é usado pela CLI no comando LIST
        """

        with self._lock:
            entries: list[str] = []

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