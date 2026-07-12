// =============================================================================
// Config.cs — Parâmetros centralizados do DFS (variante .NET/C#).
//
// Equivale ao dfs/config.py do projeto original em Python. Concentra portas,
// número de nós, fator de replicação, tamanho de chunk e limiares de heartbeat.
// =============================================================================

namespace Dfs;

public static class Config
{
    // ---- Coordenador (plano de controle) ------------------------------------
    public static readonly string CoordinatorHost =
        Environment.GetEnvironmentVariable("COORDINATOR_HOST") ?? "127.0.0.1";
    public static readonly int CoordinatorPort =
        int.TryParse(Environment.GetEnvironmentVariable("COORDINATOR_PORT"), out var p) ? p : 9100;

    // ---- Nós de armazenamento (plano de dados) ------------------------------
    // node1 -> 9101, node2 -> 9102, ...
    public static readonly int NodeCount =
        int.TryParse(Environment.GetEnvironmentVariable("NODE_COUNT"), out var n) ? n : 5;
    public const int BaseNodePort = 9101;
    public static readonly string NodeHost =
        Environment.GetEnvironmentVariable("NODE_HOST") ?? "127.0.0.1";

    // ---- Replicação e chunking ----------------------------------------------
    public const int ReplicationFactor = 3;   // cópias de cada chunk
    public const int WriteQuorum = 2;          // confirmações mínimas p/ aceitar escrita

    public const long MinChunkSize = 1L * 1024 * 1024;   // 1 MB
    public const long MaxChunkSize = 16L * 1024 * 1024;  // 16 MB
    public const int ChunkTargetMultiplier = 3;

    // ---- Detecção de falhas (heartbeat) -------------------------------------
    public const int HeartbeatInterval = 2;    // s entre batimentos
    public const int HeartbeatSuspect = 5;     // silêncio (s) p/ SUSPECT
    public const int HeartbeatDead = 12;       // silêncio (s) p/ DEAD
    public const int WatcherInterval = 2;      // varredura do supervisor

    // ---- Diretórios ---------------------------------------------------------
    // Raiz de dados = <cwd>/data (o cwd é a pasta do projeto ao usar dotnet run),
    // ou o valor de DFS_DATA se definido.
    public static readonly string BaseDir =
        Environment.GetEnvironmentVariable("DFS_DATA")
        ?? Path.Combine(Directory.GetCurrentDirectory(), "data");
    public static string MetadataDir => Path.Combine(BaseDir, "metadata");
    public static string MetadataFile => Path.Combine(MetadataDir, "metadata_index.json");
    public static string NodesDir => Path.Combine(BaseDir, "nodes");

    public record NodeInfo(string NodeId, string Host, int Port, string StorageDir);

    // Membership canônica do cluster (base do placement determinístico).
    public static List<NodeInfo> BuildNodes(int count = 0)
    {
        if (count == 0) count = NodeCount;
        var list = new List<NodeInfo>();
        for (int i = 1; i <= count; i++)
        {
            list.Add(new NodeInfo(
                $"node{i}", NodeHost, BaseNodePort + i - 1,
                Path.Combine(NodesDir, $"node{i}")));
        }
        return list;
    }

    public static List<string> NodeOrder(int count = 0) =>
        BuildNodes(count).Select(x => x.NodeId).ToList();
}
