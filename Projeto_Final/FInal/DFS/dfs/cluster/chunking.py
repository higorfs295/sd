"""
Decisão do tamanho de chunk em função do tamanho do arquivo (chunk adaptável).
"""

from dfs.config import MIN_CHUNK_SIZE, MAX_CHUNK_SIZE, CHUNK_TARGET_MULTIPLIER


def escolher_chunk_size(file_size: int, cluster_size: int) -> int:
    """
    Escolhe o tamanho do chunk para um arquivo.

    Objetivo: gerar pedaços suficientes para distribuir bem pelos nós, sem explodir o número de chunks em arquivos enormes.

    - Arquivo pequeno: 1 chunk só (menor que o piso). Não fragmenta à toa.
    - Arquivo médio/grande: ~CHUNK_TARGET_MULTIPLIER pedaços.
    - Arquivo gigante: chunks no teto (MAX), número de chunks cresce devagar.
    """
    if file_size <= 0:
        return MIN_CHUNK_SIZE

    # Ponto de partida: dividir o arquivo no número-alvo de pedaços.
    candidato = file_size // (cluster_size * CHUNK_TARGET_MULTIPLIER)

    # Garante distribuição: se o arquivo é grande o bastante para tocar
    # todos os nós, não deixa o chunk ficar tão grande que gere menos
    # pedaços que o número de nós.
    if file_size >= cluster_size * MIN_CHUNK_SIZE:
        candidato = min(candidato, file_size // cluster_size)

    # Aplica os limites absolutos (piso e teto).
    return max(MIN_CHUNK_SIZE, min(candidato, MAX_CHUNK_SIZE))
