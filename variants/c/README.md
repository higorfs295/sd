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
| Transporte de controle | gRPC unário | JSON por linha sobre TCP (sockets BSD/Winsock) |
| Transporte de dados | gRPC streaming | JSON por linha sobre TCP; bytes em base64 |
| Mensageria de cura | Apache Kafka | RPC de controle direto (`REPLICATE`) — mesmo papel |
| JSON | biblioteca | mini-parser próprio (`json.c`) |
| Metadados | JSON em disco | **Idêntico** (JSON em `data/metadata`) |
| Placement | Round-robin determinístico persistido | **Idêntico** (`replicas_for_chunk`) |
| Chunking adaptável | `chunking.py` | **Idêntico** (`choose_chunk_size`) |
| Quórum de escrita | 2 de 3 | **Idêntico** |
| Heartbeat 3 estados | ALIVE/SUSPECT/DEAD preguiçoso | **Idêntico** |
| Re-replicação, GC, elasticidade | Sim | **Sim** |
| Concorrência | threads Python | pthreads (uma thread por conexão) |

A comunicação usa apenas a biblioteca-padrão de C mais os sockets do sistema
(Winsock no Windows, BSD sockets em POSIX) e pthreads — **sem gRPC, sem Kafka,
sem dependências externas**.

---

## 2. Arquitetura

```
            plano de CONTROLE (metadados leves)
   CLI  <───────────────────────────────────►  COORDENADOR (coordinator.c)
    │        REQUEST_UPLOAD / CONFIRM /            - registro de nós + vivacidade
    │        REQUEST_DOWNLOAD / LIST / DELETE      - placement determinístico
    │        + REGISTER / HEARTBEAT (dos nós)      - metadados (JSON)
    │                                             - supervisor de re-replicação
    │  plano de DADOS (bytes dos arquivos)         - garbage collection
    └──────────────►  NÓS DE ARMAZENAMENTO  ◄─────► NÓS (fan-out entre si)
         STORE / FETCH / DELETE / LIST / REPLICATE   (node.c)
```

---

## 3. Componentes (mapa do código)

| Arquivo | Papel | Equivalente no original |
|---|---|---|
| `json.h` / `json.c` | Mini-parser/serializador JSON | (Protobuf/gRPC) |
| `dfs_common.h` / `dfs_common.c` | Config, sockets, RPC, base64, placement, chunking | `config.py` + camada gRPC + `placement.py` + `chunking.py` |
| `coordinator.c` | Plano de controle | `server.py` + `node_registry.py` + `metadata_service.py` + `replication_watcher.py` |
| `node.c` | Plano de dados | `storage_node.py` + `data_service.py` + `local_storage.py` |
| `client.c` | CLI put/get/list/rm/status | `cli.py` + `client.py` |
| `run_cluster.sh` | Orquestrador do cluster | `run_cluster.py` |
| `build.sh` / `Makefile` | Compilação | — |

---

## 4. Pré-requisitos

Um compilador C (C11) com pthreads. No Windows, o **MSYS2/MinGW-w64** (gcc) é o
caminho recomendado; em Linux/macOS, o gcc/clang do sistema. Nenhuma biblioteca
externa.

---

## 5. Como compilar

```
cd variants/c
./build.sh          # detecta o gcc do MSYS2/MinGW automaticamente
# ou:  make
```

Gera `coordinator`, `node` e `client` (com sufixo `.exe` no Windows).

> No Windows, garanta que a pasta `C:\msys64\mingw64\bin` esteja no PATH ao
> **executar** os binários (eles dependem das DLLs do runtime MinGW).

---

## 6. Como executar

### 6.1. Subir o cluster (coordenador + 5 nós)

```
./run_cluster.sh
```

Sobe o coordenador (porta 9100) e os cinco nós (portas 9101–9105) como processos
independentes. `Ctrl+C` encerra tudo.

### 6.2. Usar a CLI (em outro terminal)

```
./client put <arquivo_local> <caminho_dfs>
./client get <caminho_dfs> <arquivo_local>
./client list
./client rm  <caminho_dfs>
./client status
```

---

## 7. Como validar os requisitos

**Correção (integridade byte a byte).** Envie e baixe um arquivo e compare os
bytes — devem ser idênticos.

**Tolerância a falhas + re-replicação.** Com o cluster no ar e um arquivo
enviado, mate um dos processos de nó (portas 9101–9105). Em ~10–14 s o
coordenador marca o nó como `DEAD` (`./client status`), a re-replicação restaura
o fator 3 em nós vivos, e o `get` continua devolvendo o arquivo íntegro.

**Escalabilidade / elasticidade.** Suba um nó extra em runtime:

```
./node node6 9106 data/nodes/node6
```

---

## 8. Decisões de projeto preservadas

- **Separação controle/dados**: o coordenador só troca metadados; os bytes fluem
  direto entre CLI e nós.
- **Placement determinístico e persistido**: chunk `i` → nós `i, i+1, i+2 (mod N)`,
  decidido uma vez e gravado nos metadados; nunca recalculado.
- **Quórum de escrita (2/3)**: durabilidade garantida no `confirm`.
- **Consistência eventual**: re-replicação restaura réplicas perdidas; a coleta de
  órfãos (block report no heartbeat, confirmada em 2 ciclos) remove cópias redundantes.
- **Coordenador único**: modelo GFS/HDFS v1; recuperação relendo o índice persistido.

Para o detalhamento conceitual completo, consulte o `README.md` e o
`ARQUITETURA.md` do projeto original em Python.
