# Sistema de Arquivos Distribuído (DFS) — Variante Shellscript

Reimplementação em **bash** do Sistema de Arquivos Distribuído originalmente
escrito em Python. Preserva a **mesma natureza, arquitetura e requisitos** do
projeto de referência (coordenação centralizada com dados distribuídos, no estilo
GFS/HDFS), adaptando o transporte ao que o shell faz bem: **processos locais que
trocam mensagens pelo sistema de arquivos**.

> Projeto original: *Sistema de Arquivos Distribuído* — Sistemas Distribuídos 1,
> EMC/UFG (Vitória Mendonça e Higor Ferreira Silva).

---

## 1. O que muda em relação ao original (e o que se mantém)

| Aspecto | Original (Python) | Esta variante (bash) |
|---|---|---|
| Arquitetura | Coordenador + nós + CLI, controle/dados separados | **Idêntica** |
| **Cliente fraco + gateway** | Cliente entrega ao **ingress**; **egress** remonta | **Idêntico** (o cliente não fatia nada) |
| Transporte | gRPC (TCP) | **Caixas-postais no sistema de arquivos** (spool req/resp) |
| Mensageria de cura | Apache Kafka | Comando de controle direto (`REPLICATE`) — mesmo papel |
| **Telemetria** | Consumidor Kafka (`telemetry_hub.py`) | Coordenador agrega métricas; `telemetry.sh` ao vivo |
| Metadados | JSON em disco | arquivos-texto em `run/coordinator/meta` |
| Placement / Chunking | determinístico / adaptável | **Idênticos** (`dfs_replicas`, `dfs_chunk_size`) |
| Quórum de escrita | 2 de 3 | **Idêntico** |
| Heartbeat 3 estados / Re-replicação / GC / Elasticidade | Sim | **Sim** |
| Benchmark / testes | `benchmark_harness.py`, testes | `benchmark.sh`, `test_unit.sh`, `test_integrity.sh` |

Usa apenas utilitários POSIX comuns (bash, coreutils, grep, awk, sed, dd);
**sem gRPC, sem Kafka, sem broker**.

---

## 2. Arquitetura, transporte e o modelo ingress/egress

Cada servidor (coordenador ou nó) tem uma **caixa-postal** em disco
(`run/<servidor>/in` e `run/<servidor>/out`): um cliente grava a requisição (de
forma atômica, via `mv`) na caixa `in` e aguarda a resposta na caixa `out`. Os
bytes de arquivo trafegam como arquivos referenciados por caminho, já que todos
os processos compartilham o mesmo sistema de arquivos.

O cliente é **fraco**: não fatia arquivos, não decide posicionamento:

```
   PUT:  client --RequestUpload--> COORDENADOR  (devolve plano + INGRESS vivo)
         client --arquivo inteiro--> INGRESS --fatia+replica c/ quórum--> réplicas
                                     INGRESS --ConfirmUpload--> COORDENADOR

   GET:  client --RequestDownload--> COORDENADOR (devolve mapa + EGRESS por localidade)
         client --pede arquivo--> EGRESS --lê local + FETCH em peers--> client
```

- **Ingress**: escolhido entre os nós **vivos** (round-robin). Recebe o arquivo,
  fatia (`dd`), grava/replica com **quórum 2/3** e **confirma** ao coordenador.
- **Egress**: o nó **vivo com mais chunks** do arquivo (localidade). Remonta e devolve.

> **Nota de desempenho (Cygwin/MSYS):** como `fork()` é caro, o caminho quente
> (REGISTER/HEARTBEAT/STATUS) evita subprocessos — `printf '%(%s)T'` no lugar de
> `date`, espera via FIFO+`read` no lugar de `sleep`, parsing em bash puro.

---

## 3. Componentes (mapa do código)

| Arquivo | Papel | Equivalente no original |
|---|---|---|
| `config.sh` | Parâmetros centrais | `dfs/config.py` |
| `lib.sh` | Transporte (caixas-postais), placement, chunking | (gRPC) + `placement.py` + `chunking.py` |
| `coordinator.sh` | Controle + ingress/egress + telemetria | `server.py` + `node_registry.py` + `metadata_service.py` + `replication_watcher.py` |
| `node.sh` | Dados (ingress/egress, fan-out, réplica) | `storage_node.py` + `data_service.py` + `local_storage.py` |
| `client.sh` | CLI put/get/list/rm/status/metrics | `cli.py` + `client.py` |
| `run_cluster.sh` | Orquestrador do cluster | `run_cluster.py` |
| `telemetry.sh` | Hub de telemetria ao vivo | `telemetry_hub.py` |
| `benchmark.sh` | Benchmark de latência/throughput (CSV) | `benchmark_harness.py` + `plot_metrics.py` |
| `test_unit.sh` | Testes de placement/chunking | `test_chunking.py` |
| `test_integrity.sh` | Integridade ponta a ponta (byte a byte) | `test_node_failure.py` |

---

## 4. Pré-requisitos

Um shell **bash** com utilitários POSIX. Funciona em Linux, macOS e no Windows
via **Git Bash / MSYS2 / Cygwin**.

> **Windows (Git Bash/MSYS):** os CAMINHOS LÓGICOS do DFS (ex.: `/album/foto.jpg`)
> são convertidos em caminhos do Windows pelo MSYS. Para evitar isso, exporte
> `MSYS_NO_PATHCONV=1` antes de usar a CLI:
> `export MSYS_NO_PATHCONV=1`. Os scripts de teste/benchmark já fazem isso.

---

## 5. Como executar

### 5.1. Subir o cluster (coordenador + 5 nós)

```
cd variants/shellscript
bash run_cluster.sh     # Ctrl+C encerra
```

### 5.2. CLI (em outro terminal)

```
export MSYS_NO_PATHCONV=1          # só no Git Bash/MSYS (Windows)
bash client.sh put <arquivo_local> <caminho_dfs>   # entrega ao ingress
bash client.sh get <caminho_dfs> <arquivo_local>   # recebe do egress
bash client.sh list
bash client.sh rm  <caminho_dfs>
bash client.sh status
bash client.sh metrics
```

### 5.3. Telemetria, benchmark e testes

```
bash telemetry.sh                       # métricas ao vivo
bash benchmark.sh --sizes 1 2 5 --iter 3 # CSV em benchmark/resultados.csv
bash test_unit.sh                        # placement + chunking (sem cluster)
bash test_integrity.sh 3                 # PUT/GET + comparação byte a byte
```

---

## 6. Tolerância a falhas + re-replicação

Com um arquivo enviado, encerre um nó (o PID é registrado em `run/pids/<node>.pid`):

```
# Linux/macOS:   kill "$(cat run/pids/node5.pid)"
# Windows:       taskkill //F //T //PID "$(cat run/pids/node5.pid)"
```

Em ~10–14 s o coordenador marca o nó como `DEAD` (`bash client.sh status`), a
re-replicação restaura o fator 3, o `get` segue íntegro (servido por um egress
vivo que evita o nó morto) e o contador de re-replicações sobe.

**Elasticidade:** suba um nó extra em runtime com `bash node.sh node6`.

---

## 7. Decisões de projeto preservadas

- **Separação controle/dados** e **cliente fraco**: o coordenador só troca
  metadados; o ingress orquestra a escrita, o egress a leitura.
- **Placement determinístico e persistido**: chunk `i` → nós `i, i+1, i+2 (mod N)`.
- **Quórum de escrita (2/3)** no fan-out do ingress.
- **Consistência eventual**: re-replicação + coleta de órfãos (2 ciclos), com
  proteção do upload em andamento contra o GC.
- **Coordenador único**: modelo GFS/HDFS v1; recuperação relendo os metadados.

Para o detalhamento conceitual completo, consulte o `README.md` e o
`ARQUITETURA.md` do projeto original em Python.
