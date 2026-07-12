// =============================================================================
// Client.cs — Interface de linha de comando (CLI) da variante .NET/C#.
//
// Espelha cli.py + client.py. Cliente "fraco": fala controle com o coordenador
// e dados com os nós. Comandos: put, get, list, rm, status.
//
// PUT: RequestUpload -> envia cada chunk ao nó primary (gateway) que faz o
//      fan-out com quórum -> ConfirmUpload.
// GET (estilo GFS): pede o mapa de chunks e busca cada pedaço numa réplica viva.
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

        long chunkSize = Protocol.Long(plan, "chunk_size");
        var confirmed = new JsonArray();
        foreach (var c in (JsonArray)plan["chunks"]!)
        {
            var co = (JsonObject)c!;
            int i = Protocol.Int(co, "index");
            long offset = (long)i * chunkSize;
            int len = (int)Math.Min(chunkSize, data.LongLength - offset);
            var slice = new byte[Math.Max(0, len)];
            if (len > 0) Array.Copy(data, offset, slice, 0, len);

            var replicas = (JsonArray)co["replicas"]!;
            var primary = (JsonObject)replicas[0]!;

            var resp = Protocol.Request(Protocol.Str(primary, "host"), Protocol.Int(primary, "port"),
                new JsonObject
                {
                    ["op"] = "STORE", ["chunk_id"] = Protocol.Str(co, "chunk_id"),
                    ["data"] = Protocol.EncodeBytes(slice), ["primary"] = true,
                    ["fanout"] = replicas.DeepClone()
                });
            if (!Protocol.Bool(resp, "ok")) { Console.Error.WriteLine($"falha ao gravar chunk {i}: {Protocol.Str(resp, "error")}"); Environment.Exit(1); }

            var storedOn = ((JsonArray)resp["stored_on"]!).Select(x => x!.GetValue<string>()).ToHashSet();
            var actual = new JsonArray();
            foreach (var r in replicas)
                if (storedOn.Contains(Protocol.Str((JsonObject)r!, "node_id"))) actual.Add(r!.DeepClone());

            confirmed.Add(new JsonObject { ["index"] = i, ["chunk_id"] = Protocol.Str(co, "chunk_id"), ["replicas"] = actual });
            Console.WriteLine($"  chunk {i}: gravado em {string.Join(", ", storedOn)}");
        }

        Coord(new JsonObject { ["op"] = "CONFIRM_UPLOAD", ["path"] = dfsPath, ["chunk_size"] = chunkSize, ["chunks"] = confirmed });
        Console.WriteLine($"OK: {localPath} -> {dfsPath} ({confirmed.Count} chunk(s))");
    }

    public void Get(string dfsPath, string localPath)
    {
        var plan = Coord(new JsonObject { ["op"] = "REQUEST_DOWNLOAD", ["path"] = dfsPath });
        if (!Protocol.Bool(plan, "ok")) { Console.Error.WriteLine($"coordenador: {Protocol.Str(plan, "error")}"); Environment.Exit(1); }

        using var outFile = File.Create(localPath);
        var chunks = ((JsonArray)plan["chunks"]!).Cast<JsonObject>().OrderBy(c => Protocol.Int(c, "index"));
        foreach (var c in chunks)
        {
            var bytes = FetchChunk(c);
            if (bytes == null) { Console.Error.WriteLine($"não consegui obter o chunk {Protocol.Int(c, "index")} de nenhuma réplica viva"); Environment.Exit(1); }
            outFile.Write(bytes, 0, bytes.Length);
        }
        Console.WriteLine($"OK: {dfsPath} -> {localPath}");
    }

    private static byte[]? FetchChunk(JsonObject chunk)
    {
        foreach (var r in (JsonArray)chunk["replicas"]!)
        {
            var ro = (JsonObject)r!;
            try
            {
                var resp = Protocol.Request(Protocol.Str(ro, "host"), Protocol.Int(ro, "port"),
                    new JsonObject { ["op"] = "FETCH", ["chunk_id"] = Protocol.Str(chunk, "chunk_id") });
                if (Protocol.Bool(resp, "ok")) return Protocol.DecodeBytes(Protocol.Str(resp, "data"));
            }
            catch { /* tenta a próxima réplica */ }
        }
        return null;
    }

    public void List()
    {
        var resp = Coord(new JsonObject { ["op"] = "LIST_FILES" });
        var files = (JsonArray)resp["files"]!;
        if (files.Count == 0) { Console.WriteLine("(nenhum arquivo)"); return; }
        Console.WriteLine($"{"CAMINHO",-30} {"CHUNKS",6}  NÓS");
        foreach (var f in files.Cast<JsonObject>())
        {
            var nodes = string.Join(",", ((JsonArray)f["nodes"]!).Select(x => x!.GetValue<string>()));
            Console.WriteLine($"{Protocol.Str(f, "path"),-30} {Protocol.Int(f, "num_chunks"),6}  {nodes}");
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
        Console.WriteLine($"arquivos: {Protocol.Int(resp, "files")}");
        foreach (var n in ((JsonArray)resp["nodes"]!).Cast<JsonObject>())
            Console.WriteLine($"  {Protocol.Str(n, "node_id"),-8} {Protocol.Str(n, "state")}");
    }
}
