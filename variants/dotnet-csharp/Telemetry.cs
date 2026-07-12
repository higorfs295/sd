// =============================================================================
// Telemetry.cs — Hub de telemetria em tempo real (variante .NET/C#).
//
// Espelha o telemetry_hub.py. Sem broker Kafka, consulta o coordenador (op
// METRICS) a cada segundo e exibe as estatísticas ao vivo (mín/máx/média por
// operação, além de re-replicações e coleta de lixo).
// =============================================================================

using System.Text.Json.Nodes;

namespace Dfs;

public static class Telemetry
{
    public static void Run()
    {
        Console.WriteLine("Hub de telemetria (Ctrl+C para sair). Consultando o coordenador a cada 1s...");
        while (true)
        {
            try
            {
                var r = Protocol.Request(Config.CoordinatorHost, Config.CoordinatorPort, new JsonObject { ["op"] = "METRICS" });
                var line = $"[{DateTime.Now:HH:mm:ss}] arquivos={Protocol.Int(r, "files")} " +
                           $"re-replicações={Protocol.Int(r, "rereplications")} GC={Protocol.Int(r, "gc_deletes")}";
                foreach (var (op, mv) in (JsonObject)r["ops"]!)
                {
                    var m = (JsonObject)mv!;
                    line += $" | {op}: n={Protocol.Int(m, "count")} avg={m["avg_ms"]!.GetValue<double>()}ms " +
                            $"min={m["min_ms"]!.GetValue<double>()}ms max={m["max_ms"]!.GetValue<double>()}ms {Protocol.Long(m, "bytes")}B";
                }
                Console.WriteLine(line);
            }
            catch (Exception e) { Console.WriteLine($"[telemetria] coordenador indisponível: {e.Message}"); }
            Thread.Sleep(1000);
        }
    }
}
