// =============================================================================
// ClusterRunner.cs — Orquestrador do cluster (variante .NET/C#).
//
// Espelha run_cluster.py: sobe o coordenador e os N nós como PROCESSOS
// independentes (cada um com seu servidor TCP e seu diretório em disco).
// Cada processo é uma nova instância deste mesmo executável, com um subcomando.
// Ctrl+C encerra tudo.
// =============================================================================

using System.Diagnostics;

namespace Dfs;

public static class ClusterRunner
{
    public static void Run()
    {
        var procs = new List<Process>();
        var exePath = Environment.ProcessPath!;              // o próprio 'dfs'
        var isDll = exePath.EndsWith("dotnet", StringComparison.OrdinalIgnoreCase)
                    || exePath.EndsWith("dotnet.exe", StringComparison.OrdinalIgnoreCase);
        var dll = System.Reflection.Assembly.GetEntryAssembly()!.Location;

        Process Spawn(params string[] args)
        {
            var psi = new ProcessStartInfo { UseShellExecute = false };
            if (isDll) { psi.FileName = exePath; psi.ArgumentList.Add(dll); }
            else psi.FileName = exePath;
            foreach (var a in args) psi.ArgumentList.Add(a);
            var p = Process.Start(psi)!;
            procs.Add(p);
            return p;
        }

        Console.CancelKeyPress += (_, e) =>
        {
            e.Cancel = true;
            Console.WriteLine("\n[run_cluster] encerrando cluster...");
            foreach (var p in procs) { try { if (!p.HasExited) p.Kill(true); } catch { } }
            Environment.Exit(0);
        };

        Console.WriteLine("[run_cluster] subindo o coordenador...");
        Spawn("coordinator");
        Thread.Sleep(1500);

        foreach (var n in Config.BuildNodes())
        {
            Console.WriteLine($"[run_cluster] subindo {n.NodeId} na porta {n.Port}...");
            Spawn("node", n.NodeId, n.Port.ToString(), n.StorageDir);
            Thread.Sleep(300);
        }

        Console.WriteLine("[run_cluster] ecossistema DFS operacional. Ctrl+C para encerrar.");
        Console.WriteLine("[run_cluster] em outro terminal:  dotnet run -- client put <arquivo> /destino");
        foreach (var p in procs) p.WaitForExit();
    }
}
