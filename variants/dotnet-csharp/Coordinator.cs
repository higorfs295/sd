// =============================================================================
// Coordinator.cs — Coordenador (plano de controle) da variante .NET/C#.
//
// Espelha server.py + node_registry.py + metadata_service.py +
// replication_watcher.py. Cérebro do sistema: metadados (JSON), registro de nós
// com máquina de estados de vivacidade (ALIVE/SUSPECT/DEAD), placement
// determinístico, supervisor de re-replicação e garbage collection.
// O coordenador NUNCA toca nos bytes dos arquivos do usuário.
// =============================================================================

using System.Text.Json;
using System.Text.Json.Nodes;

namespace Dfs;

public class Coordinator
{
    private const string Alive = "ALIVE";
    private const string Suspect = "SUSPECT";
    private const string Dead = "DEAD";

    private class NodeState { public string Host = ""; public int Port; public double LastHb; public List<string> Chunks = new(); }
    public class Replica { public string node_id { get; set; } = ""; public string host { get; set; } = ""; public int port { get; set; } }
    public class ChunkEntry { public int index { get; set; } public string chunk_id { get; set; } = ""; public List<Replica> replicas { get; set; } = new(); }
    public class FileEntry { public int num_chunks { get; set; } public long chunk_size { get; set; } public double created_at { get; set; } public List<ChunkEntry> chunks { get; set; } = new(); }
    public class MetadataDoc { public Dictionary<string, FileEntry> files { get; set; } = new(); }

    private readonly object _lock = new();
    private readonly Dictionary<string, NodeState> _nodes = new();
    private readonly Dictionary<string, Dictionary<string, int>> _suspectedOrphans = new();
    private readonly Dictionary<string, List<string>> _pendingDeletes = new();
    private readonly Dictionary<string, string> _prevState = new();
    private int _fileCounter;

    private List<string> _membership;
    private readonly Dictionary<string, Config.NodeInfo> _nodeConfig = new();
    private MetadataDoc _metadata;

    private static readonly JsonSerializerOptions JsonOpts = new() { WriteIndented = true };

    public Coordinator()
    {
        _membership = Config.NodeOrder();
        foreach (var n in Config.BuildNodes()) _nodeConfig[n.NodeId] = n;
        Directory.CreateDirectory(Config.MetadataDir);
        _metadata = LoadMetadata();
    }

    public void Run()
    {
        StartWatcher();
        Log($"coordenador no ar em {Config.CoordinatorHost}:{Config.CoordinatorPort}");
        Log($"membership canônica: {string.Join(", ", _membership)} (RF={Config.ReplicationFactor}, quórum={Config.WriteQuorum})");
        Protocol.Serve(Config.CoordinatorHost, Config.CoordinatorPort, Handle);
    }

    // ---- Roteamento das chamadas do plano de controle -----------------------
    private JsonObject Handle(JsonObject req)
    {
        return Protocol.Str(req, "op") switch
        {
            "REGISTER" => HandleRegister(req),
            "HEARTBEAT" => HandleHeartbeat(req),
            "REQUEST_UPLOAD" => HandleRequestUpload(req),
            "CONFIRM_UPLOAD" => HandleConfirmUpload(req),
            "REQUEST_DOWNLOAD" => HandleRequestDownload(req),
            "DELETE_FILE" => HandleDeleteFile(req),
            "LIST_FILES" => HandleListFiles(),
            "UPDATE_REPLICAS" => HandleUpdateReplicas(req),
            "STATUS" => HandleStatus(),
            _ => new JsonObject { ["ok"] = false, ["error"] = $"op desconhecida: {Protocol.Str(req, "op")}" }
        };
    }

    private JsonObject HandleRegister(JsonObject req)
    {
        var nid = Protocol.Str(req, "node_id");
        lock (_lock)
        {
            _nodes[nid] = new NodeState { Host = Protocol.Str(req, "host"), Port = Protocol.Int(req, "port"), LastHb = Now() };
            if (!_membership.Contains(nid)) _membership.Add(nid);
            if (!_nodeConfig.ContainsKey(nid))
                _nodeConfig[nid] = new Config.NodeInfo(nid, Protocol.Str(req, "host"), Protocol.Int(req, "port"), "");
            _membership = Placement.SortNodes(_membership);
        }
        Log($"nó {nid} registrado (membership={_membership.Count})");
        return new JsonObject { ["ok"] = true, ["cluster_size"] = _membership.Count };
    }

    private JsonObject HandleHeartbeat(JsonObject req)
    {
        var nid = Protocol.Str(req, "node_id");
        var toDelete = new List<string>();
        lock (_lock)
        {
            if (!_nodes.TryGetValue(nid, out var node))
            {
                node = new NodeState { Host = _nodeConfig.GetValueOrDefault(nid)?.Host ?? "", Port = _nodeConfig.GetValueOrDefault(nid)?.Port ?? 0 };
                _nodes[nid] = node;
            }
            node.LastHb = Now();
            node.Chunks = (req["chunks"] as JsonArray)?.Select(x => x!.GetValue<string>()).ToList() ?? new List<string>();

            // Deleções pendentes (DELETE que falhou enquanto o nó estava morto).
            if (_pendingDeletes.TryGetValue(nid, out var pend)) { toDelete.AddRange(pend); _pendingDeletes.Remove(nid); }

            // Órfãos por block report, confirmados em 2 ciclos consecutivos.
            var expected = ExpectedChunksFor(nid);
            var seen = new HashSet<string>(node.Chunks);
            if (!_suspectedOrphans.TryGetValue(nid, out var suspects)) { suspects = new(); _suspectedOrphans[nid] = suspects; }
            var currentOrphans = new HashSet<string>(seen);
            currentOrphans.ExceptWith(expected);
            foreach (var cid in suspects.Keys.ToList())
                if (!currentOrphans.Contains(cid)) suspects.Remove(cid);
            foreach (var cid in currentOrphans)
            {
                suspects[cid] = suspects.GetValueOrDefault(cid) + 1;
                if (suspects[cid] >= 2) toDelete.Add(cid);
            }
        }
        return new JsonObject { ["ok"] = true, ["delete"] = ToJsonArray(toDelete.Distinct()) };
    }

    private JsonObject HandleRequestUpload(JsonObject req)
    {
        long size = Protocol.Long(req, "size");
        var uploadId = "up_" + Guid.NewGuid().ToString("N").Substring(0, 12);
        long chunkSize = ChooseChunkSize(size, _membership.Count);
        int numChunks = size <= 0 ? 1 : (int)Math.Ceiling((double)size / chunkSize);
        if (numChunks == 0) numChunks = 1;

        lock (_lock) { _fileCounter++; }

        var chunksArr = new JsonArray();
        for (int i = 0; i < numChunks; i++)
        {
            var replicaIds = Placement.ReplicasForChunk(i, _membership, Config.ReplicationFactor, _membership.Count);
            int live = replicaIds.Count(rid => StateOf(rid) != Dead);
            if (live < Config.WriteQuorum)
                return new JsonObject { ["ok"] = false, ["error"] = $"réplicas vivas insuficientes p/ quórum no chunk {i}" };

            var replicasJson = new JsonArray();
            foreach (var rid in replicaIds) replicasJson.Add(AddrJson(rid));
            chunksArr.Add(new JsonObject
            {
                ["index"] = i,
                ["chunk_id"] = $"{uploadId}_chunk_{i}",
                ["replicas"] = replicasJson,
                ["live_count"] = live
            });
        }

        Log($"upload {uploadId} p/ {Protocol.Str(req, "path")}: {numChunks} chunk(s) de {chunkSize} B");
        return new JsonObject
        {
            ["ok"] = true, ["upload_id"] = uploadId, ["chunk_size"] = chunkSize,
            ["num_chunks"] = numChunks, ["chunks"] = chunksArr
        };
    }

    private JsonObject HandleConfirmUpload(JsonObject req)
    {
        var path = Protocol.Str(req, "path");
        var entry = new FileEntry
        {
            chunk_size = Protocol.Long(req, "chunk_size"),
            created_at = Now(),
            chunks = new List<ChunkEntry>()
        };
        foreach (var c in (JsonArray)req["chunks"]!)
        {
            var co = (JsonObject)c!;
            var ce = new ChunkEntry { index = Protocol.Int(co, "index"), chunk_id = Protocol.Str(co, "chunk_id") };
            foreach (var r in (JsonArray)co["replicas"]!)
            {
                var ro = (JsonObject)r!;
                ce.replicas.Add(new Replica { node_id = Protocol.Str(ro, "node_id"), host = Protocol.Str(ro, "host"), port = Protocol.Int(ro, "port") });
            }
            entry.chunks.Add(ce);
        }
        entry.num_chunks = entry.chunks.Count;
        lock (_lock) { _metadata.files[path] = entry; PersistMetadata(); }
        Log($"arquivo {path} confirmado nos metadados");
        return new JsonObject { ["ok"] = true };
    }

    private JsonObject HandleRequestDownload(JsonObject req)
    {
        var path = Protocol.Str(req, "path");
        FileEntry? entry;
        lock (_lock) { _metadata.files.TryGetValue(path, out entry); }
        if (entry == null) return new JsonObject { ["ok"] = false, ["error"] = "arquivo não encontrado" };

        var chunksArr = new JsonArray();
        foreach (var c in entry.chunks)
        {
            var live = c.replicas.Where(r => StateOf(r.node_id) != Dead).ToList();
            var chosen = live.Count > 0 ? live : c.replicas;
            var replicasJson = new JsonArray();
            foreach (var r in chosen) replicasJson.Add(ReplicaJson(r));
            chunksArr.Add(new JsonObject { ["index"] = c.index, ["chunk_id"] = c.chunk_id, ["replicas"] = replicasJson });
        }
        return new JsonObject { ["ok"] = true, ["num_chunks"] = entry.num_chunks, ["chunks"] = chunksArr };
    }

    private JsonObject HandleDeleteFile(JsonObject req)
    {
        var path = Protocol.Str(req, "path");
        FileEntry? entry;
        lock (_lock) { _metadata.files.TryGetValue(path, out entry); }
        if (entry == null) return new JsonObject { ["ok"] = false, ["error"] = "arquivo não encontrado" };

        foreach (var c in entry.chunks)
        {
            foreach (var r in c.replicas)
            {
                if (StateOf(r.node_id) == Dead)
                    lock (_lock) { AddPending(r.node_id, c.chunk_id); }
                else
                {
                    try { Protocol.Request(r.host, r.port, new JsonObject { ["op"] = "DELETE", ["chunk_id"] = c.chunk_id }); }
                    catch { lock (_lock) { AddPending(r.node_id, c.chunk_id); } }
                }
            }
        }
        lock (_lock) { _metadata.files.Remove(path); PersistMetadata(); }
        Log($"arquivo {path} removido");
        return new JsonObject { ["ok"] = true };
    }

    private JsonObject HandleListFiles()
    {
        var arr = new JsonArray();
        lock (_lock)
        {
            foreach (var (path, e) in _metadata.files)
            {
                var nodes = e.chunks.SelectMany(c => c.replicas.Select(r => r.node_id)).Distinct().OrderBy(x => x).ToList();
                arr.Add(new JsonObject { ["path"] = path, ["num_chunks"] = e.num_chunks, ["nodes"] = ToJsonArray(nodes) });
            }
        }
        return new JsonObject { ["ok"] = true, ["files"] = arr };
    }

    private JsonObject HandleUpdateReplicas(JsonObject req)
    {
        lock (_lock)
        {
            if (_metadata.files.TryGetValue(Protocol.Str(req, "path"), out var entry))
            {
                var chunk = entry.chunks.FirstOrDefault(c => c.chunk_id == Protocol.Str(req, "chunk_id"));
                if (chunk != null && req["replicas"] is JsonArray ra)
                {
                    chunk.replicas = ra.Select(r => new Replica
                    {
                        node_id = Protocol.Str((JsonObject)r!, "node_id"),
                        host = Protocol.Str((JsonObject)r!, "host"),
                        port = Protocol.Int((JsonObject)r!, "port")
                    }).ToList();
                    PersistMetadata();
                }
            }
        }
        return new JsonObject { ["ok"] = true };
    }

    private JsonObject HandleStatus()
    {
        var arr = new JsonArray();
        foreach (var nid in _membership) arr.Add(new JsonObject { ["node_id"] = nid, ["state"] = StateOf(nid) });
        return new JsonObject { ["ok"] = true, ["nodes"] = arr, ["files"] = _metadata.files.Count };
    }

    // ---- Vivacidade (máquina de 3 estados, cálculo preguiçoso) --------------
    private string StateOf(string nodeId)
    {
        lock (_lock)
        {
            if (!_nodes.TryGetValue(nodeId, out var node) || node.LastHb == 0) return Dead;
            double silence = Now() - node.LastHb;
            if (silence < Config.HeartbeatSuspect) return Alive;
            if (silence < Config.HeartbeatDead) return Suspect;
            return Dead;
        }
    }

    private JsonObject AddrJson(string nodeId)
    {
        string host; int port;
        lock (_lock)
        {
            if (_nodes.TryGetValue(nodeId, out var n)) { host = n.Host; port = n.Port; }
            else if (_nodeConfig.TryGetValue(nodeId, out var c)) { host = c.Host; port = c.Port; }
            else { host = ""; port = 0; }
        }
        return new JsonObject { ["node_id"] = nodeId, ["host"] = host, ["port"] = port };
    }

    private static JsonObject ReplicaJson(Replica r) =>
        new() { ["node_id"] = r.node_id, ["host"] = r.host, ["port"] = r.port };

    private HashSet<string> ExpectedChunksFor(string nodeId)
    {
        var set = new HashSet<string>();
        foreach (var e in _metadata.files.Values)
            foreach (var c in e.chunks)
                if (c.replicas.Any(r => r.node_id == nodeId)) set.Add(c.chunk_id);
        return set;
    }

    private void AddPending(string nodeId, string chunkId)
    {
        if (!_pendingDeletes.TryGetValue(nodeId, out var list)) { list = new(); _pendingDeletes[nodeId] = list; }
        list.Add(chunkId);
    }

    // ---- Supervisor de re-replicação ----------------------------------------
    private void StartWatcher()
    {
        var t = new Thread(() =>
        {
            while (true)
            {
                Thread.Sleep(Config.WatcherInterval * 1000);
                try { DetectAndHeal(); }
                catch (Exception e) { Log($"watcher erro: {e.Message}"); }
            }
        }) { IsBackground = true };
        t.Start();
    }

    private void DetectAndHeal()
    {
        var transitions = new List<string>();
        lock (_lock)
        {
            foreach (var nid in _membership)
            {
                var cur = StateOf(nid);
                if (cur == Dead && _prevState.GetValueOrDefault(nid) != Dead) transitions.Add(nid);
                _prevState[nid] = cur;
            }
        }
        if (transitions.Count == 0) return;
        foreach (var nid in transitions) Log($"detectada MORTE de {nid}: iniciando re-replicação");

        var work = new List<(string path, ChunkEntry chunk, string source, string target)>();
        lock (_lock)
        {
            foreach (var (path, e) in _metadata.files)
            {
                foreach (var c in e.chunks)
                {
                    var replicaIds = c.replicas.Select(r => r.node_id).ToList();
                    var dead = replicaIds.Where(rid => StateOf(rid) == Dead).ToList();
                    if (dead.Count == 0) continue;
                    var live = replicaIds.Where(rid => StateOf(rid) != Dead).ToList();
                    if (live.Count == 0) continue;
                    int need = Config.ReplicationFactor - live.Count;
                    if (need <= 0) continue;

                    var candidates = _membership.Where(m => StateOf(m) != Dead && !replicaIds.Contains(m)).Take(need);
                    foreach (var target in candidates)
                        work.Add((path, c, live[0], target));
                }
            }
        }

        foreach (var w in work) ReplicateChunk(w.chunk, w.source, w.target);
    }

    private void ReplicateChunk(ChunkEntry chunk, string source, string target)
    {
        try
        {
            var src = AddrJson(source);
            var tgt = AddrJson(target);
            var resp = Protocol.Request(Protocol.Str(src, "host"), Protocol.Int(src, "port"),
                new JsonObject { ["op"] = "REPLICATE", ["chunk_id"] = chunk.chunk_id, ["target"] = tgt });
            if (!Protocol.Bool(resp, "ok")) return;

            lock (_lock)
            {
                chunk.replicas = chunk.replicas.Where(r => StateOf(r.node_id) != Dead).ToList();
                chunk.replicas.Add(new Replica { node_id = target, host = Protocol.Str(tgt, "host"), port = Protocol.Int(tgt, "port") });
                chunk.replicas = chunk.replicas.GroupBy(r => r.node_id).Select(g => g.First()).ToList();
                PersistMetadata();
            }
            Log($"chunk {chunk.chunk_id} re-replicado {source} -> {target}");
        }
        catch (Exception e) { Log($"falha ao re-replicar {chunk.chunk_id}: {e.Message}"); }
    }

    // ---- Tamanho de chunk adaptável (porta de chunking.py) ------------------
    private static long ChooseChunkSize(long fileSize, int clusterSize)
    {
        if (fileSize <= 0) return Config.MinChunkSize;
        long candidate = fileSize / (clusterSize * Config.ChunkTargetMultiplier);
        if (fileSize >= clusterSize * Config.MinChunkSize) candidate = Math.Min(candidate, fileSize / clusterSize);
        return Math.Max(Config.MinChunkSize, Math.Min(candidate, Config.MaxChunkSize));
    }

    // ---- Persistência de metadados ------------------------------------------
    private MetadataDoc LoadMetadata()
    {
        try
        {
            if (File.Exists(Config.MetadataFile))
                return JsonSerializer.Deserialize<MetadataDoc>(File.ReadAllText(Config.MetadataFile)) ?? new MetadataDoc();
        }
        catch { /* índice corrompido: começa vazio */ }
        return new MetadataDoc();
    }

    private void PersistMetadata() =>
        File.WriteAllText(Config.MetadataFile, JsonSerializer.Serialize(_metadata, JsonOpts));

    private static double Now() => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;

    private static JsonArray ToJsonArray(IEnumerable<string> items)
    {
        var arr = new JsonArray();
        foreach (var s in items) arr.Add(s);
        return arr;
    }

    private void Log(string msg) => Console.WriteLine($"[coordenador] {msg}");
}
