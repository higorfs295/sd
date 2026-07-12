# Sistema de Arquivos Distribuído (DFS) — Variante Ruby

Reimplementação em **Ruby** do Sistema de Arquivos Distribuído originalmente
escrito em Python. Preserva a **mesma natureza, arquitetura e requisitos** do
projeto de referência (coordenação centralizada com dados distribuídos, no estilo
GFS/HDFS), adaptando apenas a *stack* de transporte.

> Projeto original: *Sistema de Arquivos Distribuído* — Sistemas Distribuídos 1,
> EMC/UFG (Vitória Mendonça e Higor Ferreira Silva).

---

## 1. O que muda em relação ao original (e o que se mantém)

| Aspecto | Original (Python) | Esta variante (Ruby) |
|---|---|---|
| Arquitetura | Coordenador + nós + CLI, controle/dados separados | **Idêntica** |
| **Cliente fraco + gateway** | Cliente entrega o arquivo ao **ingress**; **egress** remonta | **Idêntico** (o cliente não fatia nada) |
| Transporte de controle | gRPC unário | JSON por linha sobre TCP (`lib/protocol.rb`) |
| Transporte de dados | gRPC streaming | JSON por linha sobre TCP; bytes em base64 |
| Mensageria de cura | Apache Kafka (re-replicação assíncrona) | RPC de controle direto (`REPLICATE`) — mesmo papel |
| **Telemetria** | Consumidor Kafka (`telemetry_hub.py`) | Coordenador agrega métricas; `telemetry.rb` consulta ao vivo |
| Metadados | JSON em disco | **Idêntico** |
| Placement | Round-robin determinístico persistido | **Idêntico** (`lib/placement.rb`) |
| Chunking adaptável | `chunking.py` | **Idêntico** (`choose_chunk_size`) |
| Quórum de escrita | 2 de 3 | **Idêntico** |
| Heartbeat 3 estados | ALIVE/SUSPECT/DEAD preguiçoso | **Idêntico** |
| Re-replicação, GC, elasticidade | Sim | **Sim** |
| Benchmark / testes | `benchmark_harness.py`, testes | `benchmark.rb`, `test_unit.rb`, `test_integrity.rb` |

A essência — **separação plano de controle / plano de dados, cliente fraco com
ingress/egress, chunking, placement determinístico, replicação com quórum,
detecção de falhas por heartbeat, re-replicação automática, consistência eventual
e observabilidade** — é integralmente preservada. Usa apenas a biblioteca-padrão
do Ruby (sem gRPC, sem Kafka, sem gems externas).

---

## 2. Arquitetura e o modelo ingress/egress

O cliente é **fraco**: não fatia arquivos, não decide posicionamento, não conhece
a topologia. Ele faz duas conversas por operação — uma de controle com o
coordenador e uma de dados com um nó **gateway**:

```
   PUT:  CLI --RequestUpload--> COORDENADOR  (devolve plano + INGRESS vivo)
         CLI --arquivo inteiro--> INGRESS --fatia+replica c/ quórum--> réplicas
                                  INGRESS --ConfirmUpload--> COORDENADOR

   GET:  CLI --RequestDownload--> COORDENADOR (devolve mapa + EGRESS por localidade)
         CLI --pede arquivo--> EGRESS --lê local + FETCH em peers--> CLI
```

- **Ingress**: escolhido pelo coordenador **entre os nós vivos** (round-robin por
  arquivo). Recebe o arquivo inteiro, fatia nos chunks planejados, grava os que
  lhe cabem, faz o fan-out às réplicas com **quórum 2/3** e **confirma** ao
  coordenador (por isso o cliente não confirma nada).
- **Egress**: o nó **vivo com mais chunks** do arquivo (localidade), para buscar o
  mínimo em peers. Remonta o arquivo e devolve ao cliente.

---

## 3. Componentes (mapa do código)

| Arquivo | Papel | Equivalente no original |
|---|---|---|
| `config.rb` | Parâmetros centrais | `dfs/config.py` |
| `lib/protocol.rb` | Transporte TCP + JSON por linha | (camada gRPC) |
| `lib/placement.rb` | Round-robin determinístico | `cluster/placement.py` |
| `coordinator.rb` | Plano de controle + ingress/egress + telemetria | `server.py` + `node_registry.py` + `metadata_service.py` + `replication_watcher.py` |
| `node.rb` | Plano de dados (ingress/egress, fan-out, réplica) | `storage_node.py` + `data_service.py` + `local_storage.py` |
| `client.rb` | CLI put/get/list/rm/status/metrics | `cli.py` + `client.py` |
| `run_cluster.rb` | Orquestrador do cluster | `run_cluster.py` |
| `telemetry.rb` | Hub de telemetria ao vivo | `telemetry_hub.py` |
| `benchmark.rb` | Benchmark de latência/throughput (CSV) | `benchmark_harness.py` + `plot_metrics.py` |
| `test_unit.rb` | Testes de placement/chunking | `test_chunking.py` |
| `test_integrity.rb` | Integridade ponta a ponta (SHA-256) | `test_node_failure.py` |

---

## 4. Pré-requisitos

Apenas **Ruby 3.0+** (testado em Ruby 4.0). Nenhuma gem externa. Sem Docker,
Kafka ou gRPC.

---

## 5. Como executar

### 5.1. Subir o cluster (coordenador + 5 nós)

```
cd variants/ruby
ruby run_cluster.rb
```

### 5.2. CLI (em outro terminal)

```
ruby client.rb put <arquivo_local> <caminho_dfs>   # entrega ao ingress
ruby client.rb get <caminho_dfs> <arquivo_local>   # recebe do egress
ruby client.rb list
ruby client.rb rm  <caminho_dfs>
ruby client.rb status     # estado dos nós + contadores
ruby client.rb metrics    # telemetria agregada (latências, bytes)
```

### 5.3. Telemetria ao vivo

```
ruby telemetry.rb
```

---

## 6. Como validar os requisitos

**Testes de unidade (não precisam do cluster):**

```
ruby test_unit.rb          # placement determinístico + chunking
```

**Integridade ponta a ponta (com o cluster no ar):**

```
ruby test_integrity.rb 4   # gera 4 MB, PUT, GET e compara SHA-256
```

**Benchmark de carga (com o cluster no ar):**

```
ruby benchmark.rb --sizes 1 2 5 --iter 3
# grava benchmark/resultados.csv com latência (ms) e throughput (MB/s)
```

**Tolerância a falhas + re-replicação.** Com um arquivo enviado, mate um dos
processos de nó (portas 9101–9105). Em ~10–14 s o coordenador marca o nó como
`DEAD` (`ruby client.rb status`), a re-replicação restaura o fator 3, o `get`
continua íntegro (servido por um egress vivo) e o contador de re-replicações sobe.

**Escalabilidade / elasticidade.** Suba um nó extra em runtime:

```
ruby node.rb node6 9106 ./data/nodes/node6
```

---

## 7. Decisões de projeto preservadas

- **Separação controle/dados** e **cliente fraco**: o coordenador só troca
  metadados; os bytes fluem entre a CLI e os nós gateway; o ingress orquestra a
  escrita e o egress a leitura.
- **Placement determinístico e persistido**: chunk `i` → nós `i, i+1, i+2 (mod N)`,
  decidido uma vez e gravado; nunca recalculado.
- **Quórum de escrita (2/3)** no fan-out do ingress: durabilidade no `confirm`.
- **Consistência eventual**: re-replicação restaura réplicas perdidas; a coleta de
  órfãos (block report no heartbeat, confirmada em 2 ciclos) remove redundâncias.
- **Coordenador único**: modelo GFS/HDFS v1; recuperação relendo o índice.

Para o detalhamento conceitual completo, consulte o `README.md` e o
`ARQUITETURA.md` do projeto original em Python.
