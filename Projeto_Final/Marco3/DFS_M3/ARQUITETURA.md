# ARQUITETURA.md — Sistema de Arquivos Distribuído (DFS) — Marco 3

> Documento compartilhado. Mudanças aqui, em `proto/` ou em `comum/` exigem
> combinação prévia entre os dois e entram na `main` via PR aprovado pelo outro.

---

## 1. Visão geral

DFS com coordenação centralizada e dados distribuídos, seguindo a separação
**control plane vs data plane** (decisão A.3 + B.2):

- **1 coordenador** — centraliza CONTROLE; nunca toca em bytes de arquivos.
- **5 nós de armazenamento** — centralizam DADOS; replicam entre si.
- **1+ clientes (CLI)** — cliente fraco: só lê/grava disco local e fala com
  um coordenador (controle) e um nó por operação (dados).

**Fator de replicação:** R = 3.
**Número de nós:** N = 5.

---

## 2. Componentes

### Cliente (CLI)
Processo Python no terminal do usuário. Lê arquivos do disco e envia ao sistema;
recebe e grava. Não chunkifica, não remonta, não conhece a topologia. Fala com o
coordenador (controle) e com um nó gateway por operação (dados).

### Coordenador
Processo Python único. Mantém o catálogo de metadados em memória (com
persistência em log para sobreviver a restart). Conhece os nós vivos via
heartbeat. Decide placement e designa ingress/egress por operação. Nunca toca
em bytes de arquivos de usuário.

### Nós de armazenamento
5 processos independentes (portas/diretórios distintos). Dois modos que
coexistem:
- **passivo** — armazena chunks, responde a leituras de chunk de outros nós/egress;
- **gateway** — atua como ingress ou egress quando designado pelo coordenador.

Reportam estado via heartbeat.

---

## 3. Camadas de comunicação (3 serviços gRPC)

A separação em três serviços é a materialização do princípio control vs data
plane, e mapeia 1-para-1 com a divisão de branches.

| Serviço | Implementado por | Clientes | Tráfego |
|---|---|---|---|
| **ControlService** | Coordenador | CLI (controle) e nós (registro/heartbeat) | pequeno (kB), unário |
| **DataService** | Nós | CLI (PUT/GET) | grande (MB-GB), streaming |
| **ReplicationService** | Nós | outros nós (e coordenador, p/ deleção) | grande (MB), streaming |

O `.proto` em `dfs/pb/dfs.proto` é a única fonte de verdade dos contratos.

---

## 4. Estruturas de dados

### No coordenador (memória, refletido em log)
- `nós_vivos`: `{node_id → {endereco, ultimo_heartbeat, uploads_ativos, espaco_livre}}`
- `arquivos`: `{nome_arquivo → {tamanho, num_chunks, chunks: [chunk_id]}}`
- `chunks`: `{chunk_id → {tamanho, replicas: [node_id], versao}}`
- `uploads_pendentes`: `{upload_id → {nome_arquivo, ingress, replicas, status, inicio}}`
- `downloads_pendentes`: `{download_id → {nome_arquivo, egress, status, inicio}}`

### Em cada nó (disco)
- `/storage/<node_id>/chunks/<chunk_id>.bin`
- `/storage/<node_id>/chunks/<chunk_id>.meta` (json: tamanho, versao, hash, timestamp) — *refinamento futuro; início pode ser só o `.bin`*

### Em cada nó (memória)
- `chunks_locais`: `{chunk_id → metadados}`
- `uploads_ativos`: `{upload_id → {arquivo_temporario, chunks_recebidos, replicas_alvo}}`
- `downloads_ativos`: `{download_id → {nome_arquivo, chunks_pendentes}}`

---

## 5. Estratégia de placement (round-robin determinístico)

Regra pura, sem estado, em `comum/placement.py`, importada pelos dois lados.
Dada a posição do chunk e a membership canônica dos N nós:
réplicas do chunk i = [ N[(i+0) % N], N[(i+1) % N], ..., N[(i+R-1) % N] ]

A primeira réplica da lista é o **primary**. Distribuição uniforme: num arquivo
de 10 chunks em 5 nós, cada nó recebe 6 chunks.

**Invariante crítica:** a lista passada ao placement deve ser SEMPRE a membership
canônica (os 5), na mesma ordem, NUNCA a lista de nós vivos. Liveness afeta de
qual réplica se lê / se dispara re-replicação — nunca a fórmula. As funções
aceitam `cluster_size` para validar isso e falhar alto se divergir.

### Designação de ingress
- **Round-Robin por arquivo** (`ingress_for_file`): distribui a carga de ser ingress; introduz estado (contador de arquivos no coordenador). **Implementado em `placement.py`.**

### Designação de egress
Por localidade: o nó com mais chunks do arquivo. Empate desempatado por carga (`active_downloads`).

---

## 6. Fluxo das operações

### PUT
1. CLI → Coordenador: `RequestUpload(logical_path, total_size)` → `(upload_id, ingress)`
2. CLI → Ingress: stream `UploadFile` (bytes)
3. Ingress fatia em chunks, calcula réplicas por chunk, replica em paralelo
   (`StoreChunk` nas demais réplicas)
4. Ingress → Coordenador: `ConfirmUpload(upload_id, [ChunkPlacement])`
5. Coordenador persiste metadados
6. Ingress → CLI: `UploadResult` (fim do stream)

> A confirmação parte do **ingress**, não da CLI: o ingress sabe o que replicou
> com sucesso, e tira responsabilidade do cliente fraco.

### GET
1. CLI → Coordenador: `RequestDownload(logical_path)` → `(download_id, egress, total_size)`
2. CLI → Egress: `DownloadFile(download_id)` → stream de bytes
3. Egress junta chunks (locais + busca em peers via `FetchChunk`)
4. Egress envia stream ordenado à CLI

### DELETE
1. CLI → Coordenador: `DeleteFile(logical_path)`
2. Coordenador dispara `DeleteChunk` em paralelo para todas as réplicas
3. Coordenador remove metadados, responde

### LIST
1. CLI → Coordenador: `ListFiles()`
2. Coordenador devolve lista a partir dos metadados

---

## 7. Heartbeat e detecção de falhas

- Cada nó envia heartbeat ao coordenador a cada **2 s**, com: `node_id`,
  inventário de chunks (block report), espaço livre, uploads/downloads ativos.
- Sem heartbeat por **6 s** → `SUSPECT`; por **15 s** → `DEAD`.
- `DEAD` dispara re-replicação dos chunks com fator < R.

> Para o Marco 3 (foco em balanceamento): implementar registro + heartbeat +
> carregar o block report. A re-replicação automática fica para o marco de
> tolerância a falhas — o campo já está no contrato, então é só lógica no
> coordenador depois, sem mexer no `.proto`.

---

## 8. Parâmetros do cluster

Valores reais lidos de `dfs/config.py`. Onde houver divergência com o design,
está anotado como pendência.

| Parâmetro | Valor atual (config.py) | Observação |
|---|---|---|
| N (nós) — `NODE_COUNT` | **5** | ⚠️ Ver pendência P1 abaixo. Design assume 5. |
| R (replicação) | 3 | constante de design (R=3) |
| `CHUNK_SIZE` | 64 KB (`64 * 1024`) | ⚠️ Ver pendência P2 abaixo. |
| Porta do coordenador — `PORT` | 9100 | `127.0.0.1:9100` |
| Porta base dos nós — `BASE_NODE_PORT` | 9101 | node1→9101, node2→9102, node3→9103, ... |
| Diretório de dados — `DATA_DIR` | `BASE_DIR/data` | nós em `data/nodes/nodeN/` |
| Metadados | `data/metadata/metadata_index.json` | |
| Intervalo de heartbeat | 2 s | design (a implementar) |
| Timeout SUSPECT / DEAD | 6 s / 15 s | design (a implementar) |

### ⚠️ P1 — Número de nós: config tem N=3, design assume N=5
O `config.py` está com `NODE_COUNT = 3`, mas o `.proto`, o `placement.py` e este
documento foram escritos para **N=5, R=3**. Isso importa porque:
- com N=3 e R=3, `replicas_for_chunk` retorna `min(R,N)=3` réplicas = **todos os
  nós para todo chunk**. Não há distribuição — o placement vira degenerado.
- o Marco 3 tem foco em **balanceamento**; com N=3/R=3 não há o que balancear.

**Decisão pendente:** subir `NODE_COUNT` para 5. Afeta os dois planos (o
coordenador também lê `NODE_COUNT`), então fechar junto com a Vitória.
> Atenção do próprio config: mudar `NODE_COUNT` com dados já em disco pode tornar
> arquivos antigos inacessíveis. Apagar `data/` antes de mudar.

### ⚠️ P2 — CHUNK_SIZE: um valor só vs. dois valores (PENDENTE)
Hoje o config tem um único `CHUNK_SIZE = 64 KB`, herdado do Marco 2. No modelo
gateway novo há **duas** granularidades distintas:
- **chunk oficial do DFS** — unidade de placement e replicação;
- **pedaço de transporte do stream** — quanto a CLI manda por mensagem gRPC.

Com um valor só (64 KB como chunk oficial), arquivos de poucos MB viram centenas
de chunks, cada um replicado 3x — muito overhead de metadados e de chamadas
`StoreChunk`. A alternativa é separar em dois valores no config (ex.:
`CHUNK_SIZE = 4 MB` oficial + `STREAM_PIECE_SIZE = 64 KB` transporte).

**Decisão pendente:** definir se fica um valor ou dois, e quais. O ingress
(`handle_upload_stream`) re-agrupa os pedaços de transporte em chunks oficiais —
o código precisa saber qual constante é qual.
---

## 9. Geração dos stubs gRPC

**Compilar SEMPRE a partir da raiz do projeto (`DFS_M3`), com `-I=.`:**

```bash
cd DFS_M3
python -m grpc_tools.protoc -I=. --python_out=. --grpc_python_out=. dfs/pb/dfs.proto
```

> Por quê `-I=.` e não `-I=dfs/pb`: com `-I=.` o protoc enxerga o arquivo como
> `dfs/pb/dfs.proto` e gera o import qualificado `from dfs.pb import dfs_pb2`.
> Com `-I=dfs/pb` ele gera `import dfs_pb2` (plano), que quebra com
> `ModuleNotFoundError: No module named 'dfs_pb2'` porque os stubs vivem em
> `dfs/pb/`, não na raiz do path.

Confirmar após gerar:
```bash
grep "import dfs_pb2" dfs/pb/dfs_pb2_grpc.py
# esperado: from dfs.pb import dfs_pb2 as dfs__pb2
```

Nunca editar os arquivos gerados (`dfs_pb2.py`, `dfs_pb2_grpc.py`) à mão — sempre
editar o `.proto` e regenerar.

---

## 10. Estrutura de pastas
dfs/
proto/        # .proto files (fonte de verdade dos contratos)
coordenador/  # módulo do coordenador (ControlService)
no/           # módulo do nó (DataService + ReplicationService)
cliente/      # módulo da CLI
comum/        # código compartilhado (stubs gerados, placement.py, utils)
scripts/      # iniciar cluster, etc.
ARQUITETURA.md
README.md

---

## 11. Divisão de trabalho

| Plano | Branch | Responsável | Serviços |
|---|---|---|---|
| Controle | `feature/plano-controle` | Vitória 🟦 | ControlService |
| Dados | `feature/plano-dados` | Higor 🟥 | DataService + ReplicationService |

Cada lado testa isolado com um **mock** do outro (em `tests/mocks/`):
- Vitória usa **mock de nó** (aceita heartbeat, finge armazenar).
- Higor usa **mock de coordenador** (responde `RequestUpload`/`RequestDownload`
  com listas hardcoded).

Comunicação entre os planos APENAS via: o `.proto`, a regra de placement
(`comum/placement.py`) e os IDs (`upload_id`, `download_id`, `chunk_id`).