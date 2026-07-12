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
| Transporte de controle | gRPC unário | JSON por linha sobre TCP (`Protocol.cs`) |
| Transporte de dados | gRPC streaming | JSON por linha sobre TCP; bytes em base64 |
| Mensageria de cura | Apache Kafka | RPC de controle direto (`REPLICATE`) — mesmo papel |
| Metadados | JSON em disco | **Idêntico** (`System.Text.Json`) |
| Placement | Round-robin determinístico persistido | **Idêntico** (`Placement.cs`) |
| Chunking adaptável | `chunking.py` | **Idêntico** (`ChooseChunkSize`) |
| Quórum de escrita | 2 de 3 | **Idêntico** |
| Heartbeat 3 estados | ALIVE/SUSPECT/DEAD preguiçoso | **Idêntico** |
| Re-replicação, GC, elasticidade | Sim | **Sim** |

A essência — **separação plano de controle / plano de dados, chunking,
placement determinístico, replicação com quórum, detecção de falhas por
heartbeat, re-replicação automática e consistência eventual** — é integralmente
preservada. A comunicação usa apenas a *Base Class Library* do .NET (sem gRPC,
sem Kafka, sem pacotes NuGet externos).

---

## 2. Arquitetura

```
            plano de CONTROLE (metadados leves)
   CLI  <───────────────────────────────────►  COORDENADOR (Coordinator.cs)
    │        REQUEST_UPLOAD / CONFIRM /            - registro de nós + vivacidade
    │        REQUEST_DOWNLOAD / LIST / DELETE      - placement determinístico
    │        + REGISTER / HEARTBEAT (dos nós)      - metadados (JSON)
    │                                             - supervisor de re-replicação
    │  plano de DADOS (bytes dos arquivos)         - garbage collection
    └──────────────►  NÓS DE ARMAZENAMENTO  ◄─────► NÓS (fan-out entre si)
         STORE / FETCH / DELETE / LIST / REPLICATE   (StorageNode.cs)
```

---

## 3. Componentes (mapa do código)

| Arquivo | Papel | Equivalente no original |
|---|---|---|
| `Config.cs` | Parâmetros centrais | `dfs/config.py` |
| `Protocol.cs` | Transporte TCP + JSON por linha | (camada gRPC) |
| `Placement.cs` | Round-robin determinístico | `cluster/placement.py` |
| `Coordinator.cs` | Plano de controle | `server.py` + `node_registry.py` + `metadata_service.py` + `replication_watcher.py` |
| `StorageNode.cs` | Plano de dados | `storage_node.py` + `data_service.py` + `local_storage.py` |
| `Client.cs` | CLI put/get/list/rm/status | `cli.py` + `client.py` |
| `ClusterRunner.cs` | Orquestrador do cluster | `run_cluster.py` |
| `Program.cs` | Dispatcher por subcomando | `__main__.py` |

Um único executável (`dfs`) assume papéis diferentes conforme o subcomando.

---

## 4. Pré-requisitos

Apenas o **.NET SDK 9.0+** (testado com 9.0.203). Nenhum pacote NuGet externo,
nenhum Docker, Kafka ou gRPC.

---

## 5. Como executar

### 5.1. Subir o cluster (coordenador + 5 nós)

```
cd variants/dotnet-csharp
dotnet run -- cluster
```

Sobe o coordenador (porta 9100) e os cinco nós (portas 9101–9105) como processos
independentes. Deixe a janela aberta; `Ctrl+C` encerra tudo.

### 5.2. Usar a CLI (em outro terminal)

```
dotnet run -- client put <arquivo_local> <caminho_dfs>
dotnet run -- client get <caminho_dfs> <arquivo_local>
dotnet run -- client list
dotnet run -- client rm  <caminho_dfs>
dotnet run -- client status
```

> Dica: `dotnet run` recompila a cada chamada. Para agilizar, compile uma vez com
> `dotnet build` e chame o binário direto:
> `./bin/Debug/net9.0/dfs client status`.

Exemplo completo:

```
dotnet run -- client put ./foto.jpg /album/foto.jpg
dotnet run -- client list
dotnet run -- client get /album/foto.jpg ./foto_baixada.jpg
```

---

## 6. Como validar os requisitos

**Correção (integridade byte a byte).** Envie e baixe um arquivo, e compare os
bytes — devem ser idênticos.

**Tolerância a falhas + re-replicação.** Com o cluster no ar e um arquivo
enviado, mate um dos processos de nó (portas 9101–9105). Em ~10–14 s o
coordenador marca o nó como `DEAD` (`client status`), a re-replicação restaura o
fator 3 em nós vivos, e o `get` continua devolvendo o arquivo íntegro.

**Escalabilidade / elasticidade.** Suba um nó extra em runtime — ele se registra
e passa a receber placement nos uploads seguintes:

```
dotnet run -- node node6 9106 ./data/nodes/node6
```

---

## 7. Decisões de projeto preservadas

- **Separação controle/dados**: o coordenador só troca metadados; os bytes fluem
  direto entre CLI e nós.
- **Placement determinístico e persistido**: chunk `i` → nós `i, i+1, i+2 (mod N)`,
  decidido uma vez e gravado nos metadados; nunca recalculado.
- **Quórum de escrita (2/3)**: durabilidade garantida no `confirm`.
- **Consistência eventual**: re-replicação restaura réplicas perdidas; a coleta de
  órfãos (block report no heartbeat) remove cópias redundantes em poucos ciclos.
- **Coordenador único**: modelo GFS/HDFS v1; recuperação relendo o índice persistido.

Para o detalhamento conceitual completo, consulte o `README.md` e o
`ARQUITETURA.md` do projeto original em Python.
