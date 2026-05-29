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

### Designação de ingress — A CONFIRMAR (decisão de fronteira)
Duas opções na mesa, escolher UMA e manter consistente nos dois lados + no `.proto`:
- **(a) round-robin por arquivo** (`ingress_for_file`): distribui a carga de ser
  ingress; introduz estado (contador de arquivos no coordenador). **Implementado
  em `placement.py`.**
- **(b) primary do chunk 0** (`primary_replica(0, ...)`): stateless, mas concentra
  todo ingress em N1 (gargalo). **É o que o comentário atual do `.proto` descreve.**

> ⚠️ Hoje `placement.py` faz (a) e o comentário do `.proto` descreve (b).
> Reconciliar antes da integração: corrigir o comentário do `.proto` se ficar (a).

### Designação de egress
Por localidade: o nó com mais chunks do arquivo. Empate desempatado por carga
(`active_downloads`).

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

| Parâmetro | Valor | Origem |
|---|---|---|
| N (nós) | 5 | config |
| R (replicação) | 3 | config |
| `CHUNK_SIZE` (chunk oficial do DFS) | **A DEFINIR** (sugestão do doc: 4 MB) | config / `RegisterNodeResponse` |
| Pedaço de transporte do stream | ~64 KB | CLI |
| Intervalo de heartbeat | 2 s | config |
| Timeout SUSPECT / DEAD | 6 s / 15 s | coordenador |
| Portas dos nós | **A PREENCHER** (ex.: 9101–9105) | config |

> `CHUNK_SIZE` (chunk oficial) ≠ pedaço de transporte do stream. O ingress
> re-agrupa os pedaços de transporte em chunks oficiais conforme os bytes chegam.

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

---

## 12. Decisões de fronteira em aberto — A FECHAR ANTES DA INTEGRAÇÃO

Decisões que tocam os DOIS planos e/ou o `.proto`; resolver juntos:

1. **Placement de ingress:** round-robin por arquivo (a) vs primary do chunk 0 (b).
   Ver §5. Reconciliar com o comentário do `.proto`.

2. **Egress precisa da lista de chunks do arquivo.** Hoje `RequestDownloadResponse`
   só devolve `egress` e `total_size`, não a lista de chunks. Decidir: o egress
   pergunta ao coordenador, ou o `RequestDownload` passa a devolver a lista?
   **Pode exigir mudança no `.proto`.**

3. **Ingress → coordenador (`ConfirmUpload`).** A mensagem já existe no `.proto`;
   falta decidir COMO o nó-ingress obtém o stub/endereço do coordenador
   (config fixa? vem no `RegisterNodeResponse`?).