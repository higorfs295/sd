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
            "STORE" => HandleStore(req),
            "FETCH" => HandleFetch(req),
            "DELETE" => HandleDelete(req),
            "LIST" => new JsonObject { ["ok"] = true, ["chunks"] = ToJsonArray(LocalChunks()) },
            "REPLICATE" => HandleReplicate(req),
            _ => new JsonObject { ["ok"] = false, ["error"] = $"op desconhecida: {Protocol.Str(req, "op")}" }
        };
    }

    // STORE grava um chunk. Se `primary` for true, este nó atua como gateway:
    // grava local e replica aos demais (fanout), exigindo o quórum de escrita.
    private JsonObject HandleStore(JsonObject req)
    {
        var chunkId = Protocol.Str(req, "chunk_id");
        var data = Protocol.DecodeBytes(Protocol.Str(req, "data"));
        WriteChunk(chunkId, data);

        if (!Protocol.Bool(req, "primary"))
            return new JsonObject { ["ok"] = true, ["stored_on"] = new JsonArray(_nodeId) };

        var storedOn = new List<string> { _nodeId };
        if (req["fanout"] is JsonArray fanout)
        {
            foreach (var item in fanout)
            {
                var r = (JsonObject)item!;
                if (Protocol.Str(r, "node_id") == _nodeId) continue;
                try
                {
                    var resp = Protocol.Request(Protocol.Str(r, "host"), Protocol.Int(r, "port"),
                        new JsonObject
                        {
                            ["op"] = "STORE", ["chunk_id"] = chunkId,
                            ["data"] = Protocol.Str(req, "data"), ["primary"] = false
                        });
                    if (Protocol.Bool(resp, "ok")) storedOn.Add(Protocol.Str(r, "node_id"));
                }
                catch (Exception e)
                {
                    Log($"fan-out falhou p/ {Protocol.Str(r, "node_id")}: {e.Message}");
                }
            }
        }

        if (storedOn.Count >= Config.WriteQuorum)
            return new JsonObject { ["ok"] = true, ["stored_on"] = ToJsonArray(storedOn) };
        return new JsonObject
        {
            ["ok"] = false,
            ["error"] = $"quórum não atingido ({storedOn.Count}/{Config.WriteQuorum})",
            ["stored_on"] = ToJsonArray(storedOn)
        };
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
