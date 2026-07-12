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
| Transporte | gRPC (TCP) | **Caixas-postais no sistema de arquivos** (spool de requisição/resposta) |
| Mensageria de cura | Apache Kafka | Comando de controle direto (`REPLICATE`) — mesmo papel |
| Transferência de dados | streaming gRPC | arquivos referenciados por caminho (todos compartilham o FS) |
| Metadados | JSON em disco | arquivos-texto em `run/coordinator/meta` |
| Placement | Round-robin determinístico persistido | **Idêntico** (`dfs_replicas`) |
| Chunking adaptável | `chunking.py` | **Idêntico** (`dfs_chunk_size`) |
| Quórum de escrita | 2 de 3 | **Idêntico** |
| Heartbeat 3 estados | ALIVE/SUSPECT/DEAD preguiçoso | **Idêntico** |
| Re-replicação, GC, elasticidade | Sim | **Sim** |

A essência — **separação plano de controle / plano de dados, chunking, placement
determinístico, replicação com quórum, detecção de falhas por heartbeat,
re-replicação automática, proteção de upload em andamento e coleta de órfãos** —
é integralmente preservada. Usa apenas utilitários POSIX comuns (bash, coreutils,
grep, awk, sed); **sem gRPC, sem Kafka, sem broker**.

---

## 2. Como o transporte funciona (adaptação do gRPC/Kafka)

Cada servidor (coordenador ou nó) tem uma **caixa-postal** em disco:

```
run/<servidor>/in/    <- requisições (um arquivo .req por chamada)
run/<servidor>/out/   <- respostas (um arquivo .resp por chamada)
```

Um cliente grava a requisição (de forma atômica, via `mv`) na caixa `in` do
servidor e espera o arquivo de resposta na caixa `out`. O servidor roda um laço
que consome as requisições **uma a uma** (serialização natural — sem locks) e
escreve as respostas. Os bytes de chunk trafegam como arquivos referenciados por
caminho, já que todos os processos compartilham o mesmo sistema de arquivos.

> **Nota de desempenho (Cygwin/MSYS):** como `fork()` é caro nesses ambientes, o
> caminho quente evita subprocessos — usa o builtin `printf '%(%s)T'` no lugar de
> `date`, uma espera via FIFO+`read -t` no lugar de `sleep`, e parsing de
> mensagens em bash puro no lugar de `sed`.

---

## 3. Componentes (mapa do código)

| Arquivo | Papel | Equivalente no original |
|---|---|---|
| `config.sh` | Parâmetros centrais | `dfs/config.py` |
| `lib.sh` | Transporte (caixas-postais), placement, chunking, utilidades | (camada gRPC) + `placement.py` + `chunking.py` |
| `coordinator.sh` | Plano de controle | `server.py` + `node_registry.py` + `metadata_service.py` + `replication_watcher.py` |
| `node.sh` | Plano de dados | `storage_node.py` + `data_service.py` + `local_storage.py` |
| `client.sh` | CLI put/get/list/rm/status | `cli.py` + `client.py` |
| `run_cluster.sh` | Orquestrador do cluster | `run_cluster.py` |

---

## 4. Pré-requisitos

Um shell **bash** com utilitários POSIX (coreutils, grep, awk, sed). Funciona em
Linux, macOS e em Windows via **Git Bash / MSYS2 / Cygwin**. Nenhuma dependência
externa.

---

## 5. Como executar

### 5.1. Subir o cluster (coordenador + 5 nós)

```
cd variants/shellscript
bash run_cluster.sh
```

Sobe o coordenador e os cinco nós como processos bash independentes, cada um com
sua caixa-postal e seu diretório de chunks. `Ctrl+C` encerra tudo.

### 5.2. Usar a CLI (em outro terminal)

```
bash client.sh put <arquivo_local> <caminho_dfs>
bash client.sh get <caminho_dfs> <arquivo_local>
bash client.sh list
bash client.sh rm  <caminho_dfs>
bash client.sh status
```

Exemplo:

```
bash client.sh put ./foto.jpg /album/foto.jpg
bash client.sh list
bash client.sh get /album/foto.jpg ./foto_baixada.jpg
```

---

## 6. Como validar os requisitos

**Correção (integridade byte a byte).** Envie e baixe um arquivo e compare com
`cmp` — devem ser idênticos.

**Tolerância a falhas + re-replicação.** Com um arquivo enviado, encerre um nó.
Para matá-lo de forma confiável (inclusive o laço de heartbeat), cada nó registra
seu PID em `run/pids/<node>.pid`:

```
# Linux/macOS:
kill "$(cat run/pids/node5.pid)"
# Windows (Git Bash/MSYS/Cygwin):
taskkill //F //T //PID "$(cat run/pids/node5.pid)"
```

Em ~10–14 s o coordenador marca o nó como `DEAD` (`bash client.sh status`), a
re-replicação restaura o fator 3 em nós vivos, e o `get` continua devolvendo o
arquivo íntegro.

**Escalabilidade / elasticidade.** Suba um nó extra em runtime:

```
bash node.sh node6
```

---

## 7. Decisões de projeto preservadas

- **Separação controle/dados**: o coordenador só troca metadados; os bytes fluem
  direto entre a CLI e os nós.
- **Placement determinístico e persistido**: chunk `i` → nós `i, i+1, i+2 (mod N)`,
  decidido uma vez e gravado nos metadados; nunca recalculado.
- **Quórum de escrita (2/3)**: durabilidade garantida no `confirm`.
- **Consistência eventual**: re-replicação restaura réplicas perdidas; a coleta de
  órfãos (block report no heartbeat, confirmada em 2 ciclos) remove cópias
  redundantes, com **proteção do upload em andamento** para não apagar chunks
  antes do `confirm`.
- **Coordenador único**: modelo GFS/HDFS v1; recuperação relendo os metadados.

Para o detalhamento conceitual completo, consulte o `README.md` e o
`ARQUITETURA.md` do projeto original em Python.
