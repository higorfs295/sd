"""
DESCRIÇÃO GERAL:
Esta é a camada de persistência. O `LocalStorage` é o módulo que realmente
interage com o disco rígido do sistema operacional (I/O).
Ele abstrai a manipulação de arquivos usando a biblioteca pathlib.
No futuro (em outros marcos do DFS), isso poderia ser substituído por um Cloud Storage,
e o restante do sistema não precisaria ser alterado (Princípio de Segregação/Injeção de Dependência).
"""

# Importa Path para manipulação moderna de arquivos.
from pathlib import Path

# Importa o diretório de armazenamento padrão definido nas configurações.
from dfs.config import STORAGE_DIR


class LocalStorage:
    """
    Implementa o armazenamento local de um único nó.

    No Marco 1, o sistema ainda não distribui arquivos entre múltiplos nós.
    A responsabilidade aqui é persistir arquivos em disco de forma simples.
    """

    # O construtor recebe opcionalmente a raiz do armazenamento.
    # Isso é ótimo para testes automatizados, onde você pode passar um diretório temporário.
    def __init__(self, root: Path | None = None):
        # Se nenhum diretório for informado, usa o caminho padrão do projeto.
        self.root = Path(root) if root is not None else STORAGE_DIR
        
        # Cria a pasta raiz fisicamente no disco caso ela não exista.
        # parents=True cria as pastas intermediárias necessárias. exist_ok=True evita erro se já existir.
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, path: str, data: bytes) -> None:
        """
        Salva um arquivo em disco.
        Se os diretórios intermediários não existirem, eles são criados.
        """
        # Resolve o caminho final combinando o diretório raiz com o caminho recebido (ex: "docs/teste.txt").
        target = self.root / path
        
        # Pega a pasta "pai" (ex: "docs") e garante que ela exista no sistema operacional.
        target.parent.mkdir(parents=True, exist_ok=True)
        
        # Escreve o conteúdo (em formato de bytes) diretamente no arquivo. 
        # Se o arquivo já existir, ele será sobrescrito.
        target.write_bytes(data)

    def get(self, path: str) -> bytes:
        """
        Lê um arquivo do armazenamento local e retorna seus bytes.
        """
        # Localiza o caminho completo do arquivo.
        target = self.root / path
        
        # Lê e retorna integralmente o conteúdo do arquivo em bytes (pode consumir muita RAM para arquivos enormes,
        # mas aceitável e simples para este marco do projeto).
        return target.read_bytes()

    def delete(self, path: str) -> None:
        """
        Remove um arquivo do disco.
        """
        # Localiza o caminho completo.
        target = self.root / path
        
        # Exclui o arquivo físico. Se o arquivo não existir, isso pode causar uma exceção (FileNotFoundError),
        # que será tratada na camada de serviço (FileService).
        target.unlink()

    def list_files(self) -> list[str]:
        """
        Lista todos os arquivos armazenados de forma recursiva.
        O retorno é relativo à raiz do armazenamento.
        """
        # self.root.rglob("*") busca todos os arquivos e pastas recursivamente ("globbing").
        # Usamos uma expressão geradora (for p in ... if p.is_file()) para filtrar apenas arquivos (ignorar pastas em si).
        # p.relative_to(self.root) transforma o caminho completo num caminho curto relativo 
        # (ex: C:\...\storage\docs\teste.txt vira apenas docs\teste.txt).
        # A função sorted() organiza os resultados alfabeticamente.
        return sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*")
            if p.is_file()
        )