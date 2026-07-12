// =============================================================================
// Tests.cs — Testes da variante .NET/C#.
//
//   test-unit      : funções puras (placement determinístico, chunking). Não
//                    precisa do cluster. Espelha test_chunking.py + validação de
//                    placement do original.
//   test-integrity : integridade ponta a ponta (SHA-256). Requer o cluster no ar.
//                    Espelha o teste-manchete de integridade (test_node_failure.py).
// =============================================================================

using System.Security.Cryptography;

namespace Dfs;

public static class Tests
{
    private static int _failures;
    private static void Check(string desc, Func<bool> f)
    {
        bool ok; try { ok = f(); } catch { ok = false; }
        Console.WriteLine($"{(ok ? "ok  " : "FALHA")} - {desc}");
        if (!ok) _failures++;
    }

    public static int Unit()
    {
        var nodes = new List<string> { "node1", "node2", "node3", "node4", "node5" };

        Check("chunk 0 -> node1,node2,node3", () =>
            Placement.ReplicasForChunk(0, nodes, 3, 5).SequenceEqual(new[] { "node1", "node2", "node3" }));
        Check("chunk 3 dá a volta -> node4,node5,node1", () =>
            Placement.ReplicasForChunk(3, nodes, 3, 5).SequenceEqual(new[] { "node4", "node5", "node1" }));
        Check("determinístico: mesma entrada, mesma saída", () =>
            Placement.ReplicasForChunk(7, nodes, 3).SequenceEqual(Placement.ReplicasForChunk(7, nodes, 3)));
        Check("réplicas sempre distintas", () =>
        {
            var r = Placement.ReplicasForChunk(2, nodes, 3, 5);
            return r.Distinct().Count() == r.Count;
        });
        Check("ordenação numérica: node2 antes de node10", () =>
            Placement.SortNodes(new[] { "node10", "node2", "node1" }).SequenceEqual(new[] { "node1", "node2", "node10" }));
        Check("cluster_size divergente estoura (blindagem)", () =>
        {
            try { Placement.ReplicasForChunk(0, nodes, 3, 4); return false; }
            catch (ArgumentException) { return true; }
        });

        Check("arquivo pequeno -> piso MIN_CHUNK_SIZE", () => ChunkSize(1000, 5) == Config.MinChunkSize);
        Check("chunk nunca abaixo do piso", () => ChunkSize(50L * 1024 * 1024, 5) >= Config.MinChunkSize);
        Check("chunk nunca acima do teto", () => ChunkSize(10000L * 1024 * 1024, 5) <= Config.MaxChunkSize);

        Console.WriteLine(_failures == 0 ? "\nTODOS OS TESTES PASSARAM" : $"\n{_failures} TESTE(S) FALHARAM");
        return _failures == 0 ? 0 : 1;
    }

    private static long ChunkSize(long fileSize, int clusterSize)
    {
        if (fileSize <= 0) return Config.MinChunkSize;
        long c = fileSize / (clusterSize * Config.ChunkTargetMultiplier);
        if (fileSize >= clusterSize * Config.MinChunkSize) c = Math.Min(c, fileSize / clusterSize);
        return Math.Max(Config.MinChunkSize, Math.Min(c, Config.MaxChunkSize));
    }

    public static int Integrity(int mb)
    {
        var cli = new Client();
        var tmp = Path.Combine(Path.GetTempPath(), $"dfs_integ_{Environment.ProcessId}");
        Directory.CreateDirectory(tmp);
        var src = Path.Combine(tmp, "orig.bin");
        var dst = Path.Combine(tmp, "baixado.bin");
        const string dfsPath = "/teste/integridade.bin";

        var buf = new byte[mb * 1024 * 1024];
        new Random().NextBytes(buf);
        File.WriteAllBytes(src, buf);
        var shaSrc = Sha(src);
        Console.WriteLine($"arquivo de {mb} MB, sha256={shaSrc[..16]}...");

        cli.Put(src, dfsPath);
        cli.Get(dfsPath, dst);
        var shaDst = Sha(dst);
        cli.Rm(dfsPath);
        Directory.Delete(tmp, true);

        if (shaSrc == shaDst)
        {
            Console.WriteLine("INTEGRIDADE OK: o arquivo baixado é idêntico ao enviado (byte a byte).");
            return 0;
        }
        Console.WriteLine($"FALHA DE INTEGRIDADE: {shaSrc} != {shaDst}");
        return 1;
    }

    private static string Sha(string path)
    {
        using var s = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(s)).ToLowerInvariant();
    }
}
