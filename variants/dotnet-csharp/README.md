# Sistema de Arquivos Distribuído (DFS) — Variante .NET / C#

Reimplementação em **C# (.NET 9)** do Sistema de Arquivos Distribuído
originalmente escrito em Python. Preserva a **mesma natureza, arquitetura e
requisitos** do projeto de referência (coordenação centralizada com dados
distribuídos, no estilo GFS/HDFS), adaptando apenas a *stack* de transporte.

> Projeto original: *Sistema de Arquivos Distribuído* — Sistemas Distribuídos 1,
> EMC/UFG (Vitória Mendonça e Higor Ferreira Silva).

---

## 1. O que muda em relação ao original (e o que se mantém)

| Aspecto | Original (Python) | Esta variante (C#/.NET) |
|---|---|---|
| Arquitetura | Coordenador + nós + CLI, controle/dados separados | **Idêntica** |
| **Cliente fraco + gateway** | Cliente entrega ao **ingress**; **egress** remonta | **Idêntico** (o cliente não fatia nada) |
| Transporte | gRPC (unário + streaming) | JSON por linha sobre TCP; bytes em base64 |
| Mensageria de cura | Apache Kafka | RPC de controle direto (`REPLICATE`) — mesmo papel |
| **Telemetria** | Consumidor Kafka (`telemetry_hub.py`) | Coordenador agrega métricas; `dfs telemetry` ao vivo |
| Metadados | JSON em disco | **Idêntico** (`System.Text.Json`) |
| Placement / Chunking | determinístico / adaptável | **Idênticos** (`Placement.cs`, `ChooseChunkSize`) |
| Quórum de escrita | 2 de 3 | **Idêntico** |
| Heartbeat 3 estados / Re-replicação / GC / Elasticidade | Sim | **Sim** |
| Benchmark / testes | `benchmark_harness.py`, testes | `dfs benchmark`, `dfs test-unit`, `dfs test-integrity` |

Usa apenas a *Base Class Library* do .NET — sem gRPC, sem Kafka, sem NuGet externo.

---

## 2. Arquitetura e o modelo ingress/egress

O cliente é **fraco**: não fatia arquivos, não decide posicionamento. Faz duas
conversas por operação — controle com o coordenador e dados com um **gateway**:

```
   PUT:  CLI --RequestUpload--> COORDENADOR  (devolve plano + INGRESS vivo)
         CLI --arquivo inteiro--> INGRESS --fatia+replica c/ quórum--> réplicas
                                  INGRESS --ConfirmUpload--> COORDENADOR

   GET:  CLI --RequestDownload--> COORDENADOR (devolve mapa + EGRESS por localidade)
         CLI --pede arquivo--> EGRESS --lê local + FETCH em peers--> CLI
```

- **Ingress**: escolhido entre os nós **vivos** (round-robin por arquivo). Recebe
  o arquivo, fatia, grava/replica com **quórum 2/3** e **confirma** ao coordenador.
- **Egress**: o nó **vivo com mais chunks** do arquivo (localidade). Remonta e devolve.

---

## 3. Componentes (mapa do código)

| Arquivo | Papel | Equivalente no original |
|---|---|---|
| `Config.cs` | Parâmetros centrais | `dfs/config.py` |
| `Protocol.cs` | Transporte TCP + JSON por linha | (camada gRPC) |
| `Placement.cs` | Round-robin determinístico | `cluster/placement.py` |
| `Coordinator.cs` | Controle + ingress/egress + telemetria | `server.py` + `node_registry.py` + `metadata_service.py` + `replication_watcher.py` |
| `StorageNode.cs` | Dados (ingress/egress, fan-out, réplica) | `storage_node.py` + `data_service.py` + `local_storage.py` |
| `Client.cs` | CLI put/get/list/rm/status/metrics | `cli.py` + `client.py` |
| `ClusterRunner.cs` | Orquestrador do cluster | `run_cluster.py` |
| `Telemetry.cs` | Hub de telemetria ao vivo | `telemetry_hub.py` |
| `Benchmark.cs` | Benchmark de latência/throughput (CSV) | `benchmark_harness.py` + `plot_metrics.py` |
| `Tests.cs` | Testes de placement/chunking e integridade | `test_chunking.py` + `test_node_failure.py` |
| `Program.cs` | Dispatcher por subcomando | `__main__.py` |

Um único executável (`dfs`) assume todos os papéis conforme o subcomando.

---

## 4. Pré-requisitos

Apenas o **.NET SDK 9.0+** (testado com 9.0.203). Sem NuGet externo, Docker,
Kafka ou gRPC.

---

## 5. Como executar

### 5.1. Subir o cluster (coordenador + 5 nós)

```
cd variants/dotnet-csharp
dotnet run -- cluster
```

### 5.2. CLI (em outro terminal)

```
dotnet run -- client put <arquivo_local> <caminho_dfs>   # entrega ao ingress
dotnet run -- client get <caminho_dfs> <arquivo_local>   # recebe do egress
dotnet run -- client list
dotnet run -- client rm  <caminho_dfs>
dotnet run -- client status
dotnet run -- client metrics
```

> `dotnet run` recompila a cada chamada. Para agilizar, compile uma vez com
> `dotnet build` e use o binário: `./bin/Debug/net9.0/dfs client status`.

### 5.3. Telemetria, benchmark e testes

```
dotnet run -- telemetry                       # métricas ao vivo
dotnet run -- benchmark --sizes 1 2 5 --iter 3 # CSV em benchmark/resultados.csv
dotnet run -- test-unit                        # placement + chunking (sem cluster)
dotnet run -- test-integrity 4                 # PUT/GET + SHA-256 (com cluster)
```

---

## 6. Como validar os requisitos

- **Correção**: `test-integrity` compara o SHA-256 do arquivo enviado e baixado.
- **Tolerância a falhas + re-replicação**: com um arquivo enviado, mate um nó
  (portas 9101–9105); em ~10–14 s ele fica `DEAD` (`client status`), a
  re-replicação restaura o fator 3, o `get` segue íntegro (servido por um egress
  vivo) e o contador de re-replicações sobe.
- **Escalabilidade / elasticidade**: `dotnet run -- node node6 9106 ./data/nodes/node6`.
- **Análise experimental**: `benchmark` gera latência/throughput por tamanho.

---

## 7. Decisões de projeto preservadas

- **Separação controle/dados** e **cliente fraco**: o coordenador só troca
  metadados; o ingress orquestra a escrita, o egress a leitura.
- **Placement determinístico e persistido**: chunk `i` → nós `i, i+1, i+2 (mod N)`.
- **Quórum de escrita (2/3)** no fan-out do ingress.
- **Consistência eventual**: re-replicação + coleta de órfãos (2 ciclos).
- **Coordenador único**: modelo GFS/HDFS v1; recuperação relendo o índice.

Para o detalhamento conceitual completo, consulte o `README.md` e o
`ARQUITETURA.md` do projeto original em Python.
