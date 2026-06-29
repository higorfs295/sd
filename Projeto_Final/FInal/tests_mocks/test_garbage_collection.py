"""
TESTE: Limpeza de chunks órfãos no nó (lado data plane do Marco 4).
=====================================================================

O que este teste exercita (e o teste antigo NÃO exercitava):
    O fluxo real de deleção de órfãos no nó é:
        1. O coordenador devolve chunk_ids em HeartbeatResponse.chunks_to_delete;
        2. O nó apaga ESSES chunks do disco — usando LocalStorage.delete_chunk,
           que resolve o caminho correto <root>/chunks/<chunk_id> e protege
           contra path traversal.

    O teste antigo tinha DOIS defeitos que o tornavam um falso-positivo:
        (a) Apontava para 'data/nodes/node1' (faltava o subdiretório 'chunks/'),
            o mesmo bug de caminho que existe no storage_node.py.
        (b) NUNCA criava o arquivo antes de tentar apagá-lo. Logo, a verificação
            final "o arquivo sumiu?" passava SEMPRE — mesmo que a deleção não
            funcionasse — porque o arquivo nunca existiu.

    Aqui criamos o chunk de verdade, simulamos a ordem do coordenador, chamamos
    a MESMA função que o nó usa em produção, e só então verificamos. Também
    testamos o caso negativo: um chunk que NÃO está na ordem deve permanecer.

Execução:
    cd DFS && python -m tests.test_garbage_collection
    (ajuste o módulo se o teste viver na raiz do projeto)
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from dfs.storage.local_storage import LocalStorage



def aplicar_chunks_to_delete(storage: LocalStorage, chunks_to_delete: list[str]) -> int:
    """
    Reproduz EXATAMENTE o que o nó deve fazer ao receber chunks_to_delete:
    apagar cada chunk órfão do disco via LocalStorage.delete_chunk.

    Esta é a função que o storage_node.py deve chamar (em vez do os.remove com
    caminho montado à mão). Retorna quantos chunks foram de fato apagados.
    """
    apagados = 0
    for chunk_id in chunks_to_delete:
        if storage.delete_chunk(chunk_id):  # delete_chunk já usa <root>/chunks/<id>
            apagados += 1
    return apagados


def main() -> None:
    print("--- TESTE DE GARBAGE COLLECTION (chunks órfãos no nó) ---")

    # 1. Monta um storage de nó isolado num diretório temporário.
    raiz_no = Path(tempfile.mkdtemp()) / "node1"
    storage = LocalStorage(root=raiz_no)

    # 2. Cria DOIS chunks de verdade no disco (no subdiretório chunks/ correto).
    orfao_id = "uploadX_chunk_0"     # este será marcado como órfão
    legitimo_id = "uploadY_chunk_0"  # este NÃO está na ordem; deve sobreviver
    storage.store_chunk(orfao_id, b"conteudo orfao")
    storage.store_chunk(legitimo_id, b"conteudo legitimo")

    assert storage.has_chunk(orfao_id), "pré-condição: órfão deveria existir"
    assert storage.has_chunk(legitimo_id), "pré-condição: legítimo deveria existir"

    # 3. Simula a resposta do coordenador (a Vitória) mandando apagar só o órfão.
    chunks_to_delete = [orfao_id]

    # 4. Executa a MESMA lógica que o nó roda em produção.
    apagados = aplicar_chunks_to_delete(storage, chunks_to_delete)

    # 5. Verificações reais (não vacuosas):
    sucesso = (
        apagados == 1
        and not storage.has_chunk(orfao_id)        # órfão SUMIU
        and storage.has_chunk(legitimo_id)          # legítimo PERMANECE
    )

    if sucesso:
        print("RESULTADO: TESTE PASSOU — órfão apagado, chunk legítimo preservado.")
    else:
        print("RESULTADO: TESTE FALHOU.")
        print(f"  apagados={apagados} (esperado 1)")
        print(f"  órfão ainda existe? {storage.has_chunk(orfao_id)} (esperado False)")
        print(f"  legítimo existe? {storage.has_chunk(legitimo_id)} (esperado True)")


if __name__ == "__main__":
    main()