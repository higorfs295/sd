import json  # Usada para salvar e carregar o índice dos metadados
import threading
import datetime  # Usada para registrar a data e hora do upload dos arquivos
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

    def put_file(self, path: str, total_size_bytes: int, chunks: list[dict]) -> None:
        """
        Registra um arquivo no índice, em que cada chunk tem uma LISTA de réplicas.
        Cada chunk tem {chunk_id, chunk_index, size_bytes, replicas[]}.

        O parâmetro 'chunks' é uma lista de dicionários.
        Quem chama (ConfirmUpload) converte os ChunkPlacement do .proto para esses dicionários antes de passar aqui.
        Assim, o MetadataService não precisa conhecer tipos do protobuf, mantendo a camada de metadados independente do gRPC.
        """
        # Junta todos os node_id que aparecem em qualquer réplica de qualquer chunk.
        # É o que o ListFiles mostra na coluna de nós.
        # sorted() deixa a ordem estável para o ListFiles exibir de forma previsível.
        nodes_used = sorted(
            {node_id for chunk in chunks for node_id in chunk["replicas"]}
        )

        with self._lock:  # Protege o acesso ao índice para evitar condições de corrida em uploads simultâneos
            self._index["files"][path] = {
                "path": path,
                # Guardamos como total_size_bytes (nome do campo do .proto).
                "total_size_bytes": total_size_bytes,
                "chunks": chunks,
                "uploaded_at": datetime.datetime.now().isoformat(),  # timestamp
                "distribution": {
                    "chunk_count": len(chunks),
                    "nodes_used": nodes_used,
                },
            }
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

    def update_chunk_replicas(
        self, chunk_id: str, added_node_id: str, removed_node_id: str
    ) -> bool:
        """
        Atualiza a lista de réplicas de um chunk após re-replicação bem-sucedida: remove o nó morto (removed_node_id) e adiciona o nó novo (added_node_id).

        Percorre todos os arquivos até achar o chunk (chunk_id é único no índice).
        - Retorna True se o chunk foi encontrado e atualizado;
        - False caso contrário (chunk inexistente nos metadados, que pode ter sido apagado entre a detecção e a conclusão da cópia).
        """
        with self._lock:
            for info in self._index["files"].values():
                for chunk in info.get("chunks", []):
                    if chunk["chunk_id"] != chunk_id:
                        continue
                    replicas: list[str] = chunk.get("replicas", [])
                    # Remove o nó morto se ainda estiver na lista (pode já ter sido removido por uma atualização anterior ou nunca ter estado, se os metadados divergiram).
                    if removed_node_id in replicas:
                        replicas.remove(removed_node_id)
                    # Adiciona o nó novo apenas se ainda não estiver na lista (idempotência: chamadas repetidas não duplicam a réplica).
                    if added_node_id not in replicas:
                        replicas.append(added_node_id)
                    chunk["replicas"] = replicas
                    self._save()
                    return True
        return False  # chunk não encontrado

    def expected_chunks_on(self, node_id: str) -> set[str]:
        """
        Retorna o conjunto de chunk_ids que os metadados esperam encontrar no nó dado, ou seja, todo chunk cujas réplicas registradas incluem node_id.
        É a referência contra a qual o coordenador compara o block report do heartbeat para detectar órfãos: chunks fisicamente presentes no nó mas ausentes dos metadados.

        Devolve um set para tornar a subtração de conjuntos (órfãos) direta e por elemento na comparação.
        """
        expected: set[str] = set()
        with self._lock:
            for info in self._index["files"].values():
                for chunk in info.get("chunks", []):
                    if node_id in chunk.get("replicas", []):
                        expected.add(chunk["chunk_id"])
        return expected

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
