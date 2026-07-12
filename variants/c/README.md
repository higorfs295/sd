# Sistema de Arquivos Distribuído (DFS) — Variante C

Reimplementação em **C (C11)** do Sistema de Arquivos Distribuído originalmente
escrito em Python. Preserva a **mesma natureza, arquitetura e requisitos** do
projeto de referência (coordenação centralizada com dados distribuídos, no estilo
GFS/HDFS), adaptando apenas a *stack* de transporte.

> Projeto original: *Sistema de Arquivos Distribuído* — Sistemas Distribuídos 1,
> EMC/UFG (Vitória Mendonça e Higor Ferreira Silva).

---

## 1. O que muda em relação ao original (e o que se mantém)

| Aspecto | Original (Python) | Esta variante (C) |
|---|---|---|
| Arquitetura | Coordenador + nós + CLI, controle/dados separados | **Idêntica** |
| **Cliente fraco + gateway** | Cliente entrega ao **ingress**; **egress** remonta | **Idêntico** (o cliente não fatia nada) |
| Transporte | gRPC (unário + streaming) | JSON por linha sobre TCP (Winsock/BSD); bytes em base64 |
| Mensageria de cura | Apache Kafka | RPC de controle direto (`REPLICATE`) — mesmo papel |
| **Telemetria** | Consumidor Kafka (`telemetry_hub.py`) | Coordenador agrega métricas; `client telemetry` ao vivo |
| Metadados | JSON em disco | **Idêntico** (mini-parser JSON próprio) |
| Placement / Chunking | determinístico / adaptável | **Idênticos** (`replicas_for_chunk`, `choose_chunk_size`) |
| Quórum de escrita | 2 de 3 | **Idêntico** |
| Heartbeat 3 estados / Re-replicação / GC / Elasticidade | Sim | **Sim** |
| Benchmark / testes | `benchmark_harness.py`, testes | `client benchmark`, `test_unit`, `client test-integrity` |

Sem dependências externas: apenas Winsock/BSD sockets, pthreads e um mini-parser
JSON próprio (`json.c`). Sem gRPC, sem Kafka.

---

## 2. Arquitetura e o modelo ingress/egress

O cliente é **fraco**: não fatia arquivos nem decide posicionamento. Faz duas
conversas por operação — controle com o coordenador e dados com um **gateway**:

```
   PUT:  client --RequestUpload--> COORDENADOR  (devolve plano + INGRESS vivo)
         client --arquivo inteiro--> INGRESS --fatia+replica c/ quórum--> réplicas
                                     INGRESS --ConfirmUpload--> COORDENADOR

   GET:  client --RequestDownload--> COORDENADOR (devolve mapa + EGRESS por localidade)
         client --pede arquivo--> EGRESS --lê local + FETCH em peers--> client
```

- **Ingress**: escolhido entre os nós **vivos** (round-robin por arquivo). Recebe
  o arquivo, fatia, grava/replica com **quórum 2/3** e **confirma** ao coordenador.
- **Egress**: o nó **vivo com mais chunks** do arquivo (localidade). Remonta e devolve.

---

## 3. Componentes (mapa do código)

| Arquivo | Papel | Equivalente no original |
|---|---|---|
| `dfs_common.h/.c` | Config, sockets, RPC, base64, placement, chunking | `config.py` + camada gRPC + `placement.py` + `chunking.py` |
| `json.h/.c` | Mini-parser/serializador JSON | (Protobuf) |
| `coordinator.c` | Controle + ingress/egress + telemetria | `server.py` + `node_registry.py` + `metadata_service.py` + `replication_watcher.py` |
| `node.c` | Dados (ingress/egress, fan-out, réplica) | `storage_node.py` + `data_service.py` + `local_storage.py` |
| `client.c` | CLI put/get/list/rm/status/metrics/telemetry/benchmark/test-integrity | `cli.py` + `client.py` + `benchmark_harness.py` + `telemetry_hub.py` |
| `test_unit.c` | Testes de placement/chunking | `test_chunking.py` |
| `run_cluster.sh` | Orquestrador do cluster | `run_cluster.py` |

---

## 4. Pré-requisitos

Um compilador C (GCC/Clang/MinGW). No Windows, usa-se o **MSYS2/MinGW-w64**
(`gcc`), que já traz Winsock e pthreads. Sem dependências externas.

---

## 5. Como compilar

```
cd variants/c
bash build.sh        # detecta o gcc do MSYS2/MinGW e gera os binários
# ou:  make
```

Gera `coordinator`, `node`, `client` e `test_unit` (com sufixo `.exe` no Windows).

---

## 6. Como executar

### 6.1. Subir o cluster (coordenador + 5 nós)

```
bash run_cluster.sh     # compila se necessário e sobe tudo; Ctrl+C encerra
```

### 6.2. CLI (em outro terminal)

```
./client put <arquivo_local> <caminho_dfs>   # entrega ao ingress
./client get <caminho_dfs> <arquivo_local>   # recebe do egress
./client list
./client rm  <caminho_dfs>
./client status
./client metrics
./client telemetry                            # hub de telemetria ao vivo
```

---

## 7. Como validar os requisitos

```
./test_unit                       # placement + chunking (sem cluster)
./client test-integrity 4         # PUT/GET + comparação byte a byte (com cluster)
./client benchmark --sizes 1 2 5 --iter 3   # CSV em benchmark/resultados.csv
```

- **Tolerância a falhas + re-replicação**: com um arquivo enviado, mate um nó
  (portas 9101–9105); em ~10–14 s ele fica `DEAD` (`./client status`), a
  re-replicação restaura o fator 3, o `get` segue íntegro (servido por um egress
  vivo) e o contador de re-replicações sobe.
- **Escalabilidade / elasticidade**: `./node node6 9106 data/nodes/node6`.

---

## 8. Decisões de projeto preservadas

- **Separação controle/dados** e **cliente fraco**: o coordenador só troca
  metadados; o ingress orquestra a escrita, o egress a leitura.
- **Placement determinístico e persistido**: chunk `i` → nós `i, i+1, i+2 (mod N)`.
- **Quórum de escrita (2/3)** no fan-out do ingress.
- **Consistência eventual**: re-replicação + coleta de órfãos (2 ciclos).
- **Coordenador único**: modelo GFS/HDFS v1; recuperação relendo o índice.

Para o detalhamento conceitual completo, consulte o `README.md` e o
`ARQUITETURA.md` do projeto original em Python.
