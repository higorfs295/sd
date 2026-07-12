// =============================================================================
// StorageNode.cs — Nó de armazenamento (plano de dados) da variante .NET/C#.
//
// Espelha storage_node.py + data_service.py + local_storage.py do original.
// Papéis: armazenador, réplica, gateway/primary (fan-out com quórum) e emissor
// de heartbeat (com block report). Recebe do coordenador a lista de órfãos a
// apagar (garbage collection).
// =============================================================================

using System.Text.Json.Nodes;

namespace Dfs;

public class StorageNode
{
    private readonly string _nodeId;
    private readonly string _host;
    private readonly int _port;
    private readonly string _chunksDir;

    public StorageNode(string nodeId, string host, int port, string storageDir)
    {
        _nodeId = nodeId;
        _host = host;
        _port = port;
        _chunksDir = Path.Combine(storageDir, "chunks");
        Directory.CreateDirectory(_chunksDir);
    }

    public void Run()
    {
        StartHeartbeat();
        Log($"nó no ar em {_host}:{_port} (dir={_chunksDir})");
        Protocol.Serve(_host, _port, Handle);
    }

    // ---- Roteamento das operações do plano de dados -------------------------
    private JsonObject Handle(JsonObject req)
    {
        return Protocol.Str(req, "op") switch
        {
            "PING" => new JsonObject { ["ok"] = true, ["node_id"] = _nodeId },
            "UPLOAD_FILE" => HandleUploadFile(req),     // papel de ingress
            "DOWNLOAD_FILE" => HandleDownloadFile(req), // papel de egress
            "STORE" => HandleStore(req),                // fan-out entre nós
            "FETCH" => HandleFetch(req),
            "DELETE" => HandleDelete(req),
            "LIST" => new JsonObject { ["ok"] = true, ["chunks"] = ToJsonArray(LocalChunks()) },
            "REPLICATE" => HandleReplicate(req),
            _ => new JsonObject { ["ok"] = false, ["error"] = $"op desconhecida: {Protocol.Str(req, "op")}" }
        };
    }

    // ---- Papel de INGRESS (UPLOAD_FILE) -------------------------------------
    // Recebe o arquivo inteiro + o plano; fatia, grava/replica com quórum e
    // confirma ao coordenador. Espelha DataServicer.UploadFile do original.
    private JsonObject HandleUploadFile(JsonObject req)
    {
        var sw = System.Diagnostics.Stopwatch.StartNew();
        var data = Protocol.DecodeBytes(Protocol.Str(req, "data"));
        long chunkSize = Protocol.Long(req, "chunk_size");
        var plan = (JsonArray)req["chunks"]!;
        var confirmed = new JsonArray();

        foreach (var cn in plan.Cast<JsonObject>().OrderBy(c => Protocol.Int(c, "index")))
        {
            int i = Protocol.Int(cn, "index");
            var chunkId = Protocol.Str(cn, "chunk_id");
            long offset = (long)i * chunkSize;
            int len = (int)Math.Max(0, Math.Min(chunkSize, data.LongLength - offset));
            var slice = new byte[len];
            if (len > 0) Array.Copy(data, offset, slice, 0, len);

            var replicas = (JsonArray)cn["replicas"]!;
            // grava local se este nó é uma das réplicas
            if (replicas.Cast<JsonObject>().Any(r => Protocol.Str(r, "node_id") == _nodeId))
                WriteChunk(chunkId, slice);
            // fan-out às demais réplicas, exigindo quórum
            var stored = FanOut(chunkId, slice, replicas);
            if (stored.Count < Math.Min(Config.WriteQuorum, replicas.Count))
                return new JsonObject { ["ok"] = false, ["error"] = $"quórum não atingido no chunk {i}" };

            var actual = new JsonArray();
            foreach (var r in replicas.Cast<JsonObject>())
                if (stored.Contains(Protocol.Str(r, "node_id"))) actual.Add(r.DeepClone());
            confirmed.Add(new JsonObject { ["index"] = i, ["chunk_id"] = chunkId, ["replicas"] = actual });
        }

        // O INGRESS confirma ao coordenador (cliente fraco não confirma).
        Protocol.Request(Config.CoordinatorHost, Config.CoordinatorPort,
            new JsonObject
            {
                ["op"] = "CONFIRM_UPLOAD", ["path"] = Protocol.Str(req, "path"),
                ["chunk_size"] = chunkSize, ["size"] = data.LongLength,
                ["ingress"] = _nodeId, ["chunks"] = confirmed
            });

        sw.Stop();
        EmitMetric("upload", sw.Elapsed.TotalSeconds, data.LongLength);
        Log($"ingress: {Protocol.Str(req, "path")} ({confirmed.Count} chunk(s), {data.LongLength} B) confirmado");
        return new JsonObject { ["ok"] = true, ["chunks_written"] = confirmed.Count, ["bytes"] = data.LongLength };
    }

    // Fan-out de um chunk às suas réplicas. Devolve os node_ids que confirmaram.
    private List<string> FanOut(string chunkId, byte[] data, JsonArray replicas)
    {
        var stored = new List<string>();
        if (replicas.Cast<JsonObject>().Any(r => Protocol.Str(r, "node_id") == _nodeId) && File.Exists(ChunkPath(chunkId)))
            stored.Add(_nodeId);
        var b64 = Protocol.EncodeBytes(data);
        foreach (var r in replicas.Cast<JsonObject>())
        {
            if (Protocol.Str(r, "node_id") == _nodeId) continue;
            try
            {
                var resp = Protocol.Request(Protocol.Str(r, "host"), Protocol.Int(r, "port"),
                    new JsonObject { ["op"] = "STORE", ["chunk_id"] = chunkId, ["data"] = b64 });
                if (Protocol.Bool(resp, "ok")) stored.Add(Protocol.Str(r, "node_id"));
            }
            catch (Exception e) { Log($"fan-out falhou p/ {Protocol.Str(r, "node_id")}: {e.Message}"); }
        }
        return stored;
    }

    // ---- Papel de EGRESS (DOWNLOAD_FILE) ------------------------------------
    // Reúne os chunks (locais + buscados em peers) e devolve o arquivo montado.
    private JsonObject HandleDownloadFile(JsonObject req)
    {
        var sw = System.Diagnostics.Stopwatch.StartNew();
        using var ms = new MemoryStream();
        foreach (var cn in ((JsonArray)req["chunks"]!).Cast<JsonObject>().OrderBy(c => Protocol.Int(c, "index")))
        {
            var chunkId = Protocol.Str(cn, "chunk_id");
            byte[]? bytes = File.Exists(ChunkPath(chunkId)) ? File.ReadAllBytes(ChunkPath(chunkId)) : FetchFromPeer(cn);
            if (bytes == null) return new JsonObject { ["ok"] = false, ["error"] = $"chunk {Protocol.Int(cn, "index")} indisponível" };
            ms.Write(bytes, 0, bytes.Length);
        }
        var all = ms.ToArray();
        sw.Stop();
        EmitMetric("download", sw.Elapsed.TotalSeconds, all.LongLength);
        Log($"egress: servindo {((JsonArray)req["chunks"]!).Count} chunk(s), {all.LongLength} B");
        return new JsonObject { ["ok"] = true, ["data"] = Protocol.EncodeBytes(all), ["bytes"] = all.LongLength };
    }

    private byte[]? FetchFromPeer(JsonObject chunk)
    {
        foreach (var r in ((JsonArray)chunk["replicas"]!).Cast<JsonObject>())
        {
            if (Protocol.Str(r, "node_id") == _nodeId) continue;
            try
            {
                var resp = Protocol.Request(Protocol.Str(r, "host"), Protocol.Int(r, "port"),
                    new JsonObject { ["op"] = "FETCH", ["chunk_id"] = Protocol.Str(chunk, "chunk_id") });
                if (Protocol.Bool(resp, "ok")) return Protocol.DecodeBytes(Protocol.Str(resp, "data"));
            }
            catch { /* tenta a próxima réplica */ }
        }
        return null;
    }

    private void EmitMetric(string metric, double duration, long bytes)
    {
        try
        {
            Protocol.Request(Config.CoordinatorHost, Config.CoordinatorPort,
                new JsonObject { ["op"] = "METRIC", ["metric"] = metric, ["duration"] = duration, ["bytes"] = bytes, ["node_id"] = _nodeId });
        }
        catch { /* telemetria best-effort */ }
    }

    // STORE grava um chunk vindo do fan-out de um ingress ou de uma re-replicação.
    private JsonObject HandleStore(JsonObject req)
    {
        WriteChunk(Protocol.Str(req, "chunk_id"), Protocol.DecodeBytes(Protocol.Str(req, "data")));
        return new JsonObject { ["ok"] = true, ["stored_on"] = new JsonArray(_nodeId) };
    }

    private JsonObject HandleFetch(JsonObject req)
    {
        var path = ChunkPath(Protocol.Str(req, "chunk_id"));
        if (!File.Exists(path)) return new JsonObject { ["ok"] = false, ["error"] = "chunk ausente" };
        return new JsonObject { ["ok"] = true, ["data"] = Protocol.EncodeBytes(File.ReadAllBytes(path)) };
    }

    private JsonObject HandleDelete(JsonObject req)
    {
        var path = ChunkPath(Protocol.Str(req, "chunk_id"));
        if (File.Exists(path)) File.Delete(path);
        return new JsonObject { ["ok"] = true };
    }

    // REPLICATE (cura de fundo, substitui o comando Kafka): copia um chunk local
    // para um nó de destino a pedido do coordenador.
    private JsonObject HandleReplicate(JsonObject req)
    {
        var chunkId = Protocol.Str(req, "chunk_id");
        var path = ChunkPath(chunkId);
        if (!File.Exists(path)) return new JsonObject { ["ok"] = false, ["error"] = "fonte não tem o chunk" };

        var target = (JsonObject)req["target"]!;
        var data = Protocol.EncodeBytes(File.ReadAllBytes(path));
        var resp = Protocol.Request(Protocol.Str(target, "host"), Protocol.Int(target, "port"),
            new JsonObject { ["op"] = "STORE", ["chunk_id"] = chunkId, ["data"] = data, ["primary"] = false });
        if (Protocol.Bool(resp, "ok"))
        {
            Log($"re-replicou {chunkId} -> {Protocol.Str(target, "node_id")}");
            return new JsonObject { ["ok"] = true, ["target"] = Protocol.Str(target, "node_id") };
        }
        return new JsonObject { ["ok"] = false, ["error"] = $"destino recusou: {Protocol.Str(resp, "error")}" };
    }

    // ---- Persistência local -------------------------------------------------
    private string ChunkPath(string chunkId) => Path.Combine(_chunksDir, chunkId);
    private void WriteChunk(string chunkId, byte[] data) => File.WriteAllBytes(ChunkPath(chunkId), data);

    private List<string> LocalChunks()
    {
        try { return Directory.GetFiles(_chunksDir).Select(Path.GetFileName).Where(x => x != null).Cast<string>().ToList(); }
        catch { return new List<string>(); }
    }

    // ---- Heartbeat + garbage collection -------------------------------------
    private void StartHeartbeat()
    {
        RegisterWithRetry();
        var t = new Thread(() =>
        {
            while (true)
            {
                Thread.Sleep(Config.HeartbeatInterval * 1000);
                try
                {
                    var resp = Protocol.Request(Config.CoordinatorHost, Config.CoordinatorPort,
                        new JsonObject
                        {
                            ["op"] = "HEARTBEAT", ["node_id"] = _nodeId,
                            ["chunks"] = ToJsonArray(LocalChunks())
                        });
                    if (resp["delete"] is JsonArray del)
                    {
                        foreach (var cid in del)
                        {
                            var p = ChunkPath(cid!.GetValue<string>());
                            if (File.Exists(p)) { File.Delete(p); Log($"GC apagou órfão {Path.GetFileName(p)}"); }
                        }
                    }
                }
                catch (Exception e) { Log($"heartbeat falhou: {e.Message}"); }
            }
        }) { IsBackground = true };
        t.Start();
    }

    private void RegisterWithRetry()
    {
        for (int i = 0; i < 10; i++)
        {
            try
            {
                Protocol.Request(Config.CoordinatorHost, Config.CoordinatorPort,
                    new JsonObject { ["op"] = "REGISTER", ["node_id"] = _nodeId, ["host"] = _host, ["port"] = _port });
                Log("registrado no coordenador");
                return;
            }
            catch { Thread.Sleep(1000); }
        }
        Log("não consegui registrar (coordenador fora do ar?)");
    }

    private static JsonArray ToJsonArray(IEnumerable<string> items)
    {
        var arr = new JsonArray();
        foreach (var s in items) arr.Add(s);
        return arr;
    }

    private void Log(string msg) => Console.WriteLine($"[{_nodeId}] {msg}");
}
