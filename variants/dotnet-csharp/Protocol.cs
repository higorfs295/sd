// =============================================================================
// Protocol.cs — Camada de transporte da variante .NET/C#.
//
// Adaptação: o original usa gRPC. Aqui, uma requisição = um objeto JSON numa
// linha e uma resposta = um objeto JSON numa linha, sobre TCP. Bytes de chunk
// viajam como string base64 no JSON. Preserva a natureza cliente/servidor e a
// separação controle/dados sem a dependência de gRPC.
// =============================================================================

using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Dfs;

public static class Protocol
{
    private static readonly UTF8Encoding Utf8 = new(false);

    // Envia uma requisição a host:port e devolve a resposta (JsonObject).
    public static JsonObject Request(string host, int port, JsonObject payload, int timeoutMs = 30000)
    {
        using var client = new TcpClient();
        var connect = client.ConnectAsync(host, port);
        if (!connect.Wait(timeoutMs))
            throw new TimeoutException($"timeout ao conectar em {host}:{port}");

        using var stream = client.GetStream();
        var bytes = Utf8.GetBytes(payload.ToJsonString() + "\n");
        stream.Write(bytes, 0, bytes.Length);
        stream.Flush();

        var line = ReadLine(stream, timeoutMs);
        if (line == null) throw new IOException($"conexão fechada por {host}:{port}");
        return (JsonObject)JsonNode.Parse(line)!;
    }

    // Serve conexões TCP: cada conexão lê UMA requisição JSON, chama o handler e
    // devolve a resposta JSON. Uma thread por conexão.
    public static void Serve(string host, int port, Func<JsonObject, JsonObject> handler)
    {
        var listener = new TcpListener(IPAddress.Parse(host), port);
        listener.Start();
        while (true)
        {
            var client = listener.AcceptTcpClient();
            var t = new Thread(() => HandleConnection(client, handler)) { IsBackground = true };
            t.Start();
        }
    }

    private static void HandleConnection(TcpClient client, Func<JsonObject, JsonObject> handler)
    {
        try
        {
            using (client)
            using (var stream = client.GetStream())
            {
                var line = ReadLine(stream, 60000);
                if (line == null) return;

                JsonObject resp;
                try
                {
                    var req = (JsonObject)JsonNode.Parse(line)!;
                    resp = handler(req);
                }
                catch (Exception e)
                {
                    resp = new JsonObject { ["ok"] = false, ["error"] = e.Message };
                }

                var bytes = Utf8.GetBytes(resp.ToJsonString() + "\n");
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush();
            }
        }
        catch
        {
            // cliente desconectou; ignore
        }
    }

    // Lê exatamente uma linha (terminada por \n) de um stream de rede.
    private static string? ReadLine(NetworkStream stream, int timeoutMs)
    {
        stream.ReadTimeout = timeoutMs;
        var sb = new StringBuilder();
        var buf = new byte[65536];
        var pending = new List<byte>();
        while (true)
        {
            int read;
            try { read = stream.Read(buf, 0, buf.Length); }
            catch (IOException) { return null; } // timeout
            if (read == 0) return pending.Count > 0 ? Utf8.GetString(pending.ToArray()) : null;

            for (int i = 0; i < read; i++)
            {
                if (buf[i] == (byte)'\n')
                    return Utf8.GetString(pending.ToArray());
                pending.Add(buf[i]);
            }
        }
    }

    public static string EncodeBytes(byte[] data) => Convert.ToBase64String(data);
    public static byte[] DecodeBytes(string s) => Convert.FromBase64String(s);

    // ---- Helpers de acesso ao JSON ------------------------------------------
    public static string Str(JsonObject o, string key) => o[key]?.GetValue<string>() ?? "";
    public static int Int(JsonObject o, string key) => o[key]?.GetValue<int>() ?? 0;
    public static long Long(JsonObject o, string key) => o[key]?.GetValue<long>() ?? 0;
    public static bool Bool(JsonObject o, string key) => o[key]?.GetValue<bool>() ?? false;
}
