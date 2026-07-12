// =============================================================================
// Benchmark.cs — Arcabouço de benchmark de carga (variante .NET/C#).
//
// Espelha benchmark_harness.py + plot_metrics.py (aqui sem gráficos: grava CSV e
// imprime a tabela). Para cada tamanho de arquivo, roda N iterações de PUT e GET
// medindo latência (ms) e throughput (MB/s), e grava benchmark/resultados.csv.
//
// Uso: dfs benchmark [--sizes 1 2 5] [--iter 3]   (tamanhos em MB)
// =============================================================================

using System.Diagnostics;
using System.Text;

namespace Dfs;

public static class Benchmark
{
    public static void Run(string[] args)
    {
        var sizes = new List<int> { 1, 2, 5 };
        int iter = 3;
        int si = Array.IndexOf(args, "--sizes");
        if (si >= 0)
        {
            sizes = new List<int>();
            for (int j = si + 1; j < args.Length && int.TryParse(args[j], out var v); j++) sizes.Add(v);
        }
        int ii = Array.IndexOf(args, "--iter");
        if (ii >= 0 && ii + 1 < args.Length) iter = int.Parse(args[ii + 1]);

        var cli = new Client();
        var tmp = Path.Combine(Path.GetTempPath(), $"dfs_bench_{Environment.ProcessId}");
        Directory.CreateDirectory(tmp);
        var outDir = Path.Combine(Directory.GetCurrentDirectory(), "benchmark");
        Directory.CreateDirectory(outDir);
        var csv = new StringBuilder("op,size_mb,iter,latency_ms,throughput_mbps\n");

        Console.WriteLine($"{"OP",-8} {"MB",-6} {"IT",-4} {"LATENCIA_ms",12} {"THRPUT_MBps",12}");
        var rnd = new Random(1);
        foreach (var mb in sizes)
        {
            var src = Path.Combine(tmp, $"f{mb}.bin");
            var buf = new byte[mb * 1024 * 1024];
            rnd.NextBytes(buf);
            File.WriteAllBytes(src, buf);
            var dst = Path.Combine(tmp, $"g{mb}.bin");
            var dfsPath = $"/bench/f{mb}.bin";

            for (int it = 1; it <= iter; it++)
            {
                double putMs = Timed(() => Quiet(() => cli.Put(src, dfsPath)));
                double putMbps = mb / (putMs / 1000.0);
                csv.Append($"put,{mb},{it},{putMs:F2},{putMbps:F2}\n");
                Console.WriteLine($"{"put",-8} {mb,-6} {it,-4} {putMs,12:F2} {putMbps,12:F2}");

                double getMs = Timed(() => Quiet(() => cli.Get(dfsPath, dst)));
                double getMbps = mb / (getMs / 1000.0);
                csv.Append($"get,{mb},{it},{getMs:F2},{getMbps:F2}\n");
                Console.WriteLine($"{"get",-8} {mb,-6} {it,-4} {getMs,12:F2} {getMbps,12:F2}");
            }
            Quiet(() => cli.Rm(dfsPath));
        }

        var csvPath = Path.Combine(outDir, "resultados.csv");
        File.WriteAllText(csvPath, csv.ToString());
        Directory.Delete(tmp, true);
        Console.WriteLine($"\nCSV gravado em {csvPath}");
    }

    private static double Timed(Action a)
    {
        var sw = Stopwatch.StartNew();
        a();
        sw.Stop();
        return sw.Elapsed.TotalMilliseconds;
    }

    // Silencia o stdout de um bloco (as CLIs imprimem "OK: ...").
    private static void Quiet(Action a)
    {
        var orig = Console.Out;
        Console.SetOut(TextWriter.Null);
        try { a(); }
        finally { Console.SetOut(orig); }
    }
}
