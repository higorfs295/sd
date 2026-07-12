// =============================================================================
// Program.cs — Ponto de entrada da variante .NET/C#.
//
// Um único executável ('dfs') que assume papéis diferentes conforme o subcomando:
//   dfs cluster                              -> orquestra coordenador + N nós
//   dfs coordinator                          -> sobe só o coordenador
//   dfs node <node_id> <port> <storage_dir>  -> sobe um nó de armazenamento
//   dfs client <put|get|list|rm|status|metrics> ...  -> CLI
//   dfs telemetry                            -> hub de telemetria ao vivo
//   dfs benchmark [--sizes 1 2 5] [--iter 3] -> benchmark de latência/throughput
//   dfs test-unit                            -> testes de placement/chunking
//   dfs test-integrity [MB]                  -> integridade ponta a ponta (SHA-256)
//
// Via 'dotnet run', use:  dotnet run -- <subcomando> [args...]
// =============================================================================

using Dfs;

if (args.Length == 0)
{
    Console.Error.WriteLine("uso: dfs <cluster|coordinator|node|client|telemetry|benchmark|test-unit|test-integrity> [args...]");
    return 1;
}

switch (args[0])
{
    case "cluster":
        ClusterRunner.Run();
        return 0;

    case "coordinator":
        new Coordinator().Run();
        return 0;

    case "node":
        if (args.Length < 4) { Console.Error.WriteLine("uso: dfs node <node_id> <port> <storage_dir>"); return 1; }
        new StorageNode(args[1], Config.NodeHost, int.Parse(args[2]), args[3]).Run();
        return 0;

    case "client":
        {
            if (args.Length < 2) { Console.Error.WriteLine("uso: dfs client <put|get|list|rm|status> ..."); return 1; }
            var cli = new Client();
            switch (args[1])
            {
                case "put": cli.Put(args[2], args[3]); break;
                case "get": cli.Get(args[2], args[3]); break;
                case "list": cli.List(); break;
                case "rm": cli.Rm(args[2]); break;
                case "status": cli.Status(); break;
                case "metrics": cli.Metrics(); break;
                default: Console.Error.WriteLine("subcomando de client inválido"); return 1;
            }
            return 0;
        }

    case "telemetry":
        Telemetry.Run();
        return 0;

    case "benchmark":
        Benchmark.Run(args);
        return 0;

    case "test-unit":
        return Tests.Unit();

    case "test-integrity":
        return Tests.Integrity(args.Length > 1 ? int.Parse(args[1]) : 4);

    default:
        Console.Error.WriteLine($"subcomando desconhecido: {args[0]}");
        return 1;
}
