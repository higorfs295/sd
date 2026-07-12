/* ==========================================================================
 * test_unit.c — Testes de unidade das funções puras (variante C).
 *
 * Espelha test_chunking.py + a validação de placement do original. Não precisa
 * do cluster: exercita a regra de posicionamento determinístico (round-robin),
 * a ordenação por sufixo numérico e o dimensionamento adaptável de chunk.
 *
 * Uso: test_unit
 * ========================================================================== */
#include "dfs_common.h"
#include <stdio.h>

static int failures = 0;
static void check(const char *desc, int ok) {
    printf("%s - %s\n", ok ? "ok  " : "FALHA", desc);
    if (!ok) failures++;
}

int main(void) {
    int idx[REPLICATION_FACTOR];

    int r = replicas_for_chunk(0, 5, 3, idx);
    check("chunk 0 -> node1,node2,node3 (indices 0,1,2)", r == 3 && idx[0] == 0 && idx[1] == 1 && idx[2] == 2);

    r = replicas_for_chunk(3, 5, 3, idx);
    check("chunk 3 da a volta -> indices 3,4,0", r == 3 && idx[0] == 3 && idx[1] == 4 && idx[2] == 0);

    check("replicas sempre distintas (chunk 2)", (replicas_for_chunk(2, 5, 3, idx), idx[0] != idx[1] && idx[1] != idx[2] && idx[0] != idx[2]));

    check("ordenacao numerica: node2 < node10", node_id_cmp("node2", "node10") < 0);
    check("ordenacao numerica: node10 > node2", node_id_cmp("node10", "node2") > 0);

    check("arquivo pequeno -> piso MIN_CHUNK_SIZE", choose_chunk_size(1000, 5) == MIN_CHUNK_SIZE);
    check("chunk nunca abaixo do piso", choose_chunk_size(50L * 1024 * 1024, 5) >= MIN_CHUNK_SIZE);
    check("chunk nunca acima do teto", choose_chunk_size(300L * 1024 * 1024, 5) <= MAX_CHUNK_SIZE);

    printf(failures ? "\n%d TESTE(S) FALHARAM\n" : "\nTODOS OS TESTES PASSARAM\n", failures);
    return failures ? 1 : 0;
}
