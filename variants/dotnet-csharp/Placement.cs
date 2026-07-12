// =============================================================================
// Placement.cs — Regra de posicionamento determinístico (round-robin).
//
// Porta fiel de placement.py. Função pura: dada a MEMBERSHIP CANÔNICA (os N nós,
// na mesma ordem) e o índice do chunk, devolve sempre a mesma lista de réplicas.
//
//   réplicas do chunk i = [ nodes[(i+0) % N], nodes[(i+1) % N], ... (R vezes) ]
//
// A primeira réplica é o primary. INVARIANTE: passe SEMPRE a membership canônica
// (os N), nunca só os vivos — senão o % N muda e o placement inteiro desloca.
// =============================================================================

using System.Text.RegularExpressions;

namespace Dfs;

public static class Placement
{
    // Ordena os node_ids pelo sufixo numérico ("node2" < "node10"), estável.
    public static List<string> SortNodes(IEnumerable<string> nodeIds)
    {
        return nodeIds
            .OrderBy(nid =>
            {
                var m = Regex.Match(nid, @"(\d+)$");
                return m.Success ? 0 : 1;
            })
            .ThenBy(nid =>
            {
                var m = Regex.Match(nid, @"(\d+)$");
                return m.Success ? int.Parse(m.Value) : 0;
            })
            .ThenBy(nid => nid, StringComparer.Ordinal)
            .ToList();
    }

    // Réplicas do chunk de índice chunkIndex.
    public static List<string> ReplicasForChunk(int chunkIndex, IEnumerable<string> nodeIds,
        int replicationFactor = 3, int clusterSize = -1)
    {
        if (chunkIndex < 0) throw new ArgumentException($"chunk_index negativo: {chunkIndex}");

        var ordered = SortNodes(nodeIds);
        if (clusterSize >= 0 && ordered.Count != clusterSize)
            throw new ArgumentException(
                $"cluster divergente: esperado {clusterSize}, recebido {ordered.Count}. " +
                "O placement EXIGE a membership canônica, não os nós vivos.");
        if (ordered.Count == 0) return new List<string>();

        int n = ordered.Count;
        int r = Math.Min(replicationFactor, n);
        var result = new List<string>();
        for (int offset = 0; offset < r; offset++)
            result.Add(ordered[(chunkIndex + offset) % n]);
        return result;
    }

    public static string? PrimaryForChunk(int chunkIndex, IEnumerable<string> nodeIds,
        int replicationFactor = 3, int clusterSize = -1)
    {
        var r = ReplicasForChunk(chunkIndex, nodeIds, replicationFactor, clusterSize);
        return r.Count > 0 ? r[0] : null;
    }

    // Ingress de um arquivo: round-robin ENTRE arquivos (contador monotônico no
    // coordenador), para nenhum nó virar gargalo eterno de entrada.
    public static string? IngressForFile(int fileIndex, IEnumerable<string> nodeIds)
    {
        var ordered = SortNodes(nodeIds);
        return ordered.Count == 0 ? null : ordered[fileIndex % ordered.Count];
    }
}
