// =============================================================================
// Client.cs — Interface de linha de comando (CLI) da variante .NET/C#.
//
// Espelha cli.py + client.py. CLIENTE FRACO: não fatia arquivos nem decide
// posicionamento. Fala controle com o coordenador (para achar ingress/egress) e
// entrega/recebe o arquivo INTEIRO a/do nó gateway.
//
// PUT: RequestUpload -> envia o arquivo ao INGRESS (que fatia, replica e confirma).
// GET: RequestDownload -> pede o arquivo ao EGRESS (que remonta por localidade).
// =============================================================================

using System.Text.Json.Nodes;

namespace Dfs;

public class Client
{
    private static JsonObject Coord(JsonObject payload) =>
        Protocol.Request(Config.CoordinatorHost, Config.CoordinatorPort, payload);

    public void Put(string localPath, string dfsPath)
    {
        if (!File.Exists(localPath)) { Console.Error.WriteLine($"arquivo local não existe: {localPath}"); Environment.Exit(1); }

        var data = File.ReadAllBytes(localPath);
        var plan = Coord(new JsonObject { ["op"] = "REQUEST_UPLOAD", ["path"] = dfsPath, ["size"] = data.LongLength });
        if (!Protocol.Bool(plan, "ok")) { Console.Error.WriteLine($"coordenador recusou: {Protocol.Str(plan, "error")}"); Environment.Exit(1); }

        var ingress = (JsonObject)plan["ingress"]!;
        // Entrega o arquivo INTEIRO ao ingress; ele fatia, replica e confirma.
        var resp = Protocol.Request(Protocol.Str(ingress, "host"), Protocol.Int(ingress, "port"),
            new JsonObject
            {
                ["op"] = "UPLOAD_FILE", ["path"] = dfsPath,
                ["upload_id"] = Protocol.Str(plan, "upload_id"), ["chunk_size"] = Protocol.Long(plan, "chunk_size"),
                ["chunks"] = plan["chunks"]!.DeepClone(), ["data"] = Protocol.EncodeBytes(data)
            });
        if (!Protocol.Bool(resp, "ok")) { Console.Error.WriteLine($"falha no upload (ingress {Protocol.Str(ingress, "node_id")}): {Protocol.Str(resp, "error")}"); Environment.Exit(1); }

        Console.WriteLine($"OK: {localPath} -> {dfsPath} via ingress {Protocol.Str(ingress, "node_id")} " +
                          $"({Protocol.Int(resp, "chunks_written")} chunk(s), {Protocol.Long(resp, "bytes")} B)");
    }

    public void Get(string dfsPath, string localPath)
    {
        var plan = Coord(new JsonObject { ["op"] = "REQUEST_DOWNLOAD", ["path"] = dfsPath });
        if (!Protocol.Bool(plan, "ok")) { Console.Error.WriteLine($"coordenador: {Protocol.Str(plan, "error")}"); Environment.Exit(1); }

        var egress = (JsonObject)plan["egress"]!;
        var resp = Protocol.Request(Protocol.Str(egress, "host"), Protocol.Int(egress, "port"),
            new JsonObject { ["op"] = "DOWNLOAD_FILE", ["path"] = dfsPath, ["chunks"] = plan["chunks"]!.DeepClone() });
        if (!Protocol.Bool(resp, "ok")) { Console.Error.WriteLine($"falha no download (egress {Protocol.Str(egress, "node_id")}): {Protocol.Str(resp, "error")}"); Environment.Exit(1); }

        File.WriteAllBytes(localPath, Protocol.DecodeBytes(Protocol.Str(resp, "data")));
        Console.WriteLine($"OK: {dfsPath} -> {localPath} via egress {Protocol.Str(egress, "node_id")} ({Protocol.Long(resp, "bytes")} B)");
    }

    public void List()
    {
        var resp = Coord(new JsonObject { ["op"] = "LIST_FILES" });
        var files = (JsonArray)resp["files"]!;
        if (files.Count == 0) { Console.WriteLine("(nenhum arquivo)"); return; }
        Console.WriteLine($"{"CAMINHO",-28} {"CHUNKS",6} {"BYTES",10}  NÓS");
        foreach (var f in files.Cast<JsonObject>())
        {
            var nodes = string.Join(",", ((JsonArray)f["nodes"]!).Select(x => x!.GetValue<string>()));
            Console.WriteLine($"{Protocol.Str(f, "path"),-28} {Protocol.Int(f, "num_chunks"),6} {Protocol.Long(f, "size"),10}  {nodes}");
        }
    }

    public void Rm(string dfsPath)
    {
        var resp = Coord(new JsonObject { ["op"] = "DELETE_FILE", ["path"] = dfsPath });
        if (!Protocol.Bool(resp, "ok")) { Console.Error.WriteLine($"erro: {Protocol.Str(resp, "error")}"); Environment.Exit(1); }
        Console.WriteLine($"removido: {dfsPath}");
    }

    public void Status()
    {
        var resp = Coord(new JsonObject { ["op"] = "STATUS" });
        Console.WriteLine($"arquivos: {Protocol.Int(resp, "files")} | re-replicações: {Protocol.Int(resp, "rereplications")} | GC: {Protocol.Int(resp, "gc_deletes")}");
        foreach (var n in ((JsonArray)resp["nodes"]!).Cast<JsonObject>())
            Console.WriteLine($"  {Protocol.Str(n, "node_id"),-8} {Protocol.Str(n, "state")}");
    }

    public void Metrics()
    {
        var resp = Coord(new JsonObject { ["op"] = "METRICS" });
        Console.WriteLine($"arquivos: {Protocol.Int(resp, "files")} | re-replicações: {Protocol.Int(resp, "rereplications")} | GC apagou: {Protocol.Int(resp, "gc_deletes")}");
        var ops = (JsonObject)resp["ops"]!;
        if (ops.Count == 0) { Console.WriteLine("(sem métricas de operação ainda)"); return; }
        Console.WriteLine($"{"OP",-10} {"N",6} {"AVG(ms)",10} {"MIN(ms)",10} {"MAX(ms)",10} {"BYTES",12}");
        foreach (var (op, mv) in ops)
        {
            var m = (JsonObject)mv!;
            Console.WriteLine($"{op,-10} {Protocol.Int(m, "count"),6} {m["avg_ms"]!.GetValue<double>(),10:F2} {m["min_ms"]!.GetValue<double>(),10:F2} {m["max_ms"]!.GetValue<double>(),10:F2} {Protocol.Long(m, "bytes"),12}");
        }
    }
}
