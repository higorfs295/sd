# test_elasticity_addition.py
"""
Prova, em memória, que a adição dinâmica funciona:
- um nó inédito (node6, fora do config) entra na membership canônica;
- o registro é idempotente (re-registrar não duplica);
- o placement de uploads FUTUROS passa a distribuir para o node6.
NÃO sobe processos nem Kafka: testa só a lógica do plano de controle.
"""
from dfs.cluster.node_registry import NodeRegistry
from dfs.cluster import placement
from dfs.config import REPLICATION_FACTOR


def main() -> None:
    reg = NodeRegistry()  # node1..node5, vindos do config
    assert reg.size() == 5, f"esperava 5 nós do config, veio {reg.size()}"

    # node6 INÉDITO se registra em runtime (não existe no config.py).
    reg.register_node("node6", "127.0.0.1", 9106, free_space_bytes=10**12)

    membros, n = reg.canonical_snapshot()
    assert n == 6, f"após adicionar node6, esperava 6, veio {n}"
    assert any(m.node_id == "node6" for m in membros), "node6 não entrou na canônica"

    # Idempotência: re-registrar (ex.: node6 reiniciou) NÃO pode duplicar.
    reg.register_node("node6", "127.0.0.1", 9106, free_space_bytes=10**12)
    assert reg.size() == 6, "re-registro duplicou o node6 na membership"

    # Placement de uploads futuros agora cobre 6 nós; node6 deve aparecer.
    apareceu = set()
    for chunk_index in range(12):
        reps = placement.replicas_for_chunk(
            chunk_index=chunk_index,
            nodes=membros,
            replication_factor=REPLICATION_FACTOR,
            cluster_size=n,
        )
        apareceu.update(r.node_id for r in reps)
    assert "node6" in apareceu, "node6 deveria receber chunks de uploads futuros"

    print("OK: adição dinâmica funciona — node6 entrou na canônica e recebe placement.")


if __name__ == "__main__":
    main()
