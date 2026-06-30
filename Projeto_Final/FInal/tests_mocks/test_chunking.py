"""
Testes da decisão de tamanho de chunk adaptável (escolher_chunk_size).

Validam PROPRIEDADES da função, não números mágicos: assim o teste continua
válido independentemente dos valores exatos de MIN/MAX/alvo escolhidos no
config.py. Os limites são importados do próprio config, então o teste se
adapta à configuração real do projeto.

Roda isolado, em memória, sem subir cluster nem Kafka.
"""
import sys
from pathlib import Path

# O pacote 'dfs' vive em Final/DFS/. Este teste vive em Final/tests_mocks/.
# Inserimos Final/DFS/ no path para o 'import dfs...' resolver de qualquer pasta.
_AQUI = Path(__file__).resolve()
_DFS_DIR = _AQUI.parent.parent / "DFS"
if str(_DFS_DIR) not in sys.path:
    sys.path.insert(0, str(_DFS_DIR))

from dfs.cluster.chunking import escolher_chunk_size
from dfs.config import MIN_CHUNK_SIZE, MAX_CHUNK_SIZE, NODE_COUNT

KB = 1024
MB = 1024 * 1024
GB = 1024 * 1024 * 1024


def test_resultado_sempre_dentro_dos_limites():
    """Para qualquer tamanho positivo, o chunk fica entre MIN e MAX (inclusive)."""
    for file_size in [1 * KB, 500 * KB, 1 * MB, 10 * MB, 100 * MB, 1 * GB, 10 * GB]:
        cs = escolher_chunk_size(file_size, cluster_size=NODE_COUNT)
        assert MIN_CHUNK_SIZE <= cs <= MAX_CHUNK_SIZE, (
            f"chunk fora dos limites para {file_size} bytes: {cs}"
        )


def test_arquivo_minusculo_vira_um_chunk():
    """Arquivo menor que o piso não deve ser fatiado: o chunk cobre o arquivo todo."""
    file_size = 50 * KB
    cs = escolher_chunk_size(file_size, cluster_size=NODE_COUNT)
    # chunk_size >= file_size garante um único chunk (não fragmenta à toa).
    assert cs >= file_size
    assert cs == MIN_CHUNK_SIZE  # cai no piso


def test_arquivo_gigante_respeita_o_teto():
    """Arquivo enorme deve ser limitado pelo teto, para não estourar memória/straggler."""
    cs = escolher_chunk_size(5 * GB, cluster_size=NODE_COUNT)
    assert cs == MAX_CHUNK_SIZE


def test_arquivo_medio_distribui_por_todos_os_nos():
    """Arquivo grande o bastante deve gerar chunks suficientes para tocar todos os nós."""
    file_size = 60 * MB
    cs = escolher_chunk_size(file_size, cluster_size=NODE_COUNT)
    n_chunks = file_size // cs
    assert n_chunks >= NODE_COUNT, (
        f"esperado >= {NODE_COUNT} chunks para distribuir, obtido {n_chunks}"
    )


def test_funcao_e_nao_decrescente():
    """Arquivo maior nunca deve receber chunk menor (monotonicidade)."""
    tamanhos = [1 * MB, 10 * MB, 100 * MB, 1 * GB, 10 * GB]
    anterior = 0
    for file_size in tamanhos:
        cs = escolher_chunk_size(file_size, cluster_size=NODE_COUNT)
        assert cs >= anterior, "chunk diminuiu para um arquivo maior"
        anterior = cs


def test_determinista():
    """Mesma entrada deve sempre produzir a mesma saída."""
    a = escolher_chunk_size(123 * MB, cluster_size=NODE_COUNT)
    b = escolher_chunk_size(123 * MB, cluster_size=NODE_COUNT)
    assert a == b


def test_tamanho_zero_nao_quebra():
    """Arquivo de tamanho zero (caso de borda) não deve estourar exceção."""
    cs = escolher_chunk_size(0, cluster_size=NODE_COUNT)
    assert cs >= MIN_CHUNK_SIZE


def _tabela_demonstrativa():
    """Imprime o comportamento da função para vários tamanhos (apoio à apresentação)."""
    print(f"\n{'arquivo':>12} | {'chunk_size':>12} | {'nº chunks':>10}")
    print("-" * 40)
    for rotulo, file_size in [
        ("50 KB", 50 * KB),
        ("5 MB", 5 * MB),
        ("60 MB", 60 * MB),
        ("500 MB", 500 * MB),
        ("5 GB", 5 * GB),
    ]:
        cs = escolher_chunk_size(file_size, cluster_size=NODE_COUNT)
        n = max(1, -(-file_size // cs))  # ceil da divisão
        print(f"{rotulo:>12} | {cs / MB:>9.2f} MB | {n:>10}")
    print()


if __name__ == "__main__":
    test_resultado_sempre_dentro_dos_limites()
    test_arquivo_minusculo_vira_um_chunk()
    test_arquivo_gigante_respeita_o_teto()
    test_arquivo_medio_distribui_por_todos_os_nos()
    test_funcao_e_nao_decrescente()
    test_determinista()
    test_tamanho_zero_nao_quebra()
    _tabela_demonstrativa()
    print("OK: chunk adaptável funciona — todas as propriedades validadas.")
