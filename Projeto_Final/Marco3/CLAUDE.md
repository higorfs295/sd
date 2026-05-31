# CLAUDE.md — DFS Marco 3 (Projeto Final Sistemas Distribuídos)

> Instruções sobre o estado atual do projeto.
> Leia antes de qualquer tarefa. Não altere sem combinar com a Vitória.

---

## O que é este projeto

Sistema de Arquivos Distribuído (DFS) acadêmico em Python, desenvolvido em dupla:
- **Vitória** — plano de controle (coordenador, metadados, heartbeat) → branch `feature/plano-controle`
- **Higor** — plano de dados (nós de armazenamento, streaming, replicação) → branch `feature/plano-dados`

Disciplina: Sistemas Distribuídos — Prof. Vagner José Sacramento Rodrigues — UFG/INF

---

## Arquitetura (decisão A.3 + B.2)

- **1 coordenador** — só metadados e decisões, NUNCA toca em bytes de arquivos
- **5 nós de armazenamento** — armazenam e replicam chunks; atuam como ingress ou egress quando designados
- **1+ clientes (CLI)** — cliente fraco: faz duas chamadas por operação (coordenador + nó gateway)
- **Fator de replicação R = 3**, cluster com N = 5 nós
- **Placement**: round-robin determinístico por índice de chunk — `réplicas[i] = [N(i%5), N((i+1)%5), N((i+2)%5)]`
- **3 serviços gRPC separados**: `ControlService` (coordenador), `DataService` (nós↔CLI), `ReplicationService` (nós↔nós)

---

## Organização do código — POR CAMADA (não por ator)

A estrutura real do projeto organiza por papel técnico, não por componente.
Isso define ONDE cada coisa nova deve morar:

- `dfs/interface/` — adaptadores gRPC e pontos de entrada de processo.
  Aqui vivem as classes `...Servicer`. `server.py` = coordenador,
  `storage_node.py` = nó, `cli.py` = cliente.
- `dfs/application/` — lógica de negócio (regras, sem detalhe de rede).
  `file_service.py`, `metadata_service.py`, `node_service.py`.
- `dfs/cluster/` — infraestrutura de cluster compartilhada.
  `node_registry.py`, `placement.py`, `node_client.py`, `sharding.py`.
- `dfs/storage/` — persistência física (`local_storage.py`).
- `dfs/pb/` — contrato (`dfs.proto`) e stubs gerados.

Padrão a seguir: o servicer (adaptador gRPC, em `interface/`) DELEGA para um
serviço de lógica (em `application/`). Ex.: `CoordinatorServicer` → `FileService`.

> NÃO criar `dfs/coordenador/`, `dfs/no/`, `dfs/comum/`. A estrutura por ator
> que aparecia em versões antigas dos `.md` foi abandonada — a estrutura real
> por camada é a que vale.

---

## Convenção de execução

Arquivos dentro de `DFS_M3/` que importam `dfs` PRECISAM rodar em modo módulo,
a partir de `DFS_M3/`:

```bash
cd DFS_M3
python -m scripts.start_coordinator   # sobe só o coordenador
python -m tests.test_list_files       # roda um teste
```

Rodar `python tests/test_list_files.py` direto QUEBRA o `import dfs` (o Python
põe `tests/` no path em vez de `DFS_M3/`). O `-m` resolve isso.

`scripts/` = executar o sistema. `tests/` = verificar o sistema (prefixo
`test_` é o que o pytest reconhece).

---

## Estado atual do código — O QUE JÁ EXISTE

### Fundação (pronta e estável — não alterar sem combinar com o Higor)
- `dfs/pb/dfs.proto` — contrato dos 3 serviços gRPC completo e compilado
- `dfs/pb/dfs_pb2.py` e `dfs_pb2_grpc.py` — stubs gerados (NUNCA editar à mão)
- `dfs/cluster/placement.py` — regra round-robin determinística implementada e comentada
- `dfs/config.py` — `NODE_COUNT = 5`, portas 9100 (coordenador) + 9101–9105 (nós)
- `ARQUITETURA.md` — decisões de design documentadas

### Camada de aplicação (código legado do Marco 2, em migração)
- `dfs/application/file_service.py` — **ATENÇÃO: ainda usa o modelo antigo**
  - O PUT ainda passa bytes pelo coordenador (violação da separação control/data plane)
  - O GET já foi parcialmente migrado: devolve um chunk map JSON no campo `message` para a CLI buscar direto nos nós
  - Usa `ShardingManager` (hash-based) em vez do novo `placement.py` (round-robin)
  - Usa `FileRequest`/`FileResponse` do serviço legado `DFSService`, não os novos serviços do `.proto`
- `dfs/application/metadata_service.py` — funcional, persiste em `data/metadata/metadata_index.json`
- `dfs/application/node_service.py` — funcional para o serviço legado `DFSService`
- `dfs/cluster/node_client.py` — usa `DFSServiceStub` (legado)
- `dfs/cluster/node_registry.py` — estático, lê de `config.py`; ainda não tem `RegisterNode` dinâmico
- `dfs/cluster/sharding.py` — hash-based (Marco 2); será substituído pelo `placement.py`
- `dfs/client.py` — CLI usa `DFSServiceStub` (legado); precisa migrar para `ControlServiceStub`
- `dfs/interface/server.py` — coordenador. Em migração: passa a hospedar `DFSService` (legado) **e** `ControlService` (novo) no mesmo socket
- `dfs/interface/storage_node.py` — expõe `DFSServiceServicer` (legado); precisa expor `DataServiceServicer` + `ReplicationServiceServicer` (lado do Higor)
- `dfs/interface/cli.py` — funcional com o modelo atual
- `dfs/storage/local_storage.py` — funcional, sem mudanças previstas

### Infraestrutura
- `run_cluster.py` (em `Marco3/`, fora de `DFS_M3/`) — sobe o cluster INTEIRO (N nós + coordenador). Runner de integração. Depende de estar um nível acima de `DFS_M3/` (calcula o path por conta própria) — NÃO mover para dentro de `DFS_M3/`, quebra.
- `run_cli.py` (em `Marco3/`) — ponto de entrada da CLI
- `scripts/start_coordinator.py` — sobe SÓ o coordenador. Runner de isolamento; é o usado no dia a dia para testar o plano de controle sem depender dos nós do Higor.

---

## O que está pendente (próximos passos — branch da Vitória)

### Prioridade 1 — Migrar o coordenador para o novo `.proto`
Adicionar a classe `ControlServiceServicer` DENTRO de `dfs/interface/server.py`,
ao lado do `CoordinatorServicer` legado. O servidor registra os DOIS serviços no
mesmo socket durante a migração — o caminho antigo segue de pé enquanto o novo é
construído RPC a RPC. Quando o `ControlService` estiver completo e a CLI migrada,
remove-se o `DFSService`.

As 7 RPCs do `ControlService`:
   - `RegisterNode` — adicionar nó ao registro dinâmico
   - `Heartbeat` — atualizar status, marcar SUSPECT/DEAD por timeout
   - `RequestUpload` — escolher ingress via `placement.ingress_for_file()`, gerar `upload_id`, responder
   - `ConfirmUpload` — receber do ingress a lista de chunks, persistir via `MetadataService`
   - `RequestDownload` — escolher egress por localidade, responder com `download_id`
   - `DeleteFile` — disparar `DeleteChunk` nos nós via `ReplicationService`, remover metadados
   - `ListFiles` — retornar lista do `MetadataService`

Ordem de implementação (do menos para o mais acoplado):
   1. **Esqueleto + fiação** — as 7 RPCs respondendo `UNIMPLEMENTED`, servidor registrando o `ControlService`. ✅ em andamento
   2. **`ListFiles`** — só lê o `MetadataService` (que já existe). 1ª RPC de verdade. ✅ em andamento
   3. `RegisterNode` + `Heartbeat` + NodeRegistry dinâmico (ver P2/P3)
   4. Mock de nó (ver P4)
   5. `RequestUpload` + `ConfirmUpload` (depende da decisão de ingress)
   6. `RequestDownload` (egress por localidade)
   7. `DeleteFile` (dispara `DeleteChunk`; precisa do mock ou do Higor)

> Quando a lógica de controle crescer (registro dinâmico, rastreio de heartbeat,
> decisões de upload/download), extrair para um serviço NOVO em `dfs/application/`
> e fazer o servicer delegar — como `CoordinatorServicer` → `FileService`. Para o
> `ListFiles` de agora não precisa: ele só lê o `MetadataService`.

Depois (não agora): migrar `client.py` para `ControlServiceStub` e `cli.py` para
o fluxo de duas chamadas (coordenador + nó gateway).

### Prioridade 2 — NodeRegistry dinâmico
Evoluir `dfs/cluster/node_registry.py` (NÃO criar outro). Hoje é estático (lê de
`config.py`). Precisa aceitar `RegisterNode` e atualizar status via heartbeat.
Pode manter o config como bootstrap inicial.

### Prioridade 3 — Heartbeat com detecção de falhas
- Nós enviam heartbeat a cada 2 s com `chunk_ids` (block report)
- Coordenador marca SUSPECT após 6 s sem heartbeat, DEAD após 15 s
- Re-replicação automática fica para o marco de tolerância a falhas — o campo já está no contrato

### Prioridade 4 — Mock de nó para testes isolados
Criar `tests/mocks/mock_node.py` que implementa `DataServiceServicer` e
`ReplicationServiceServicer` com respostas fixas, para testar o plano de controle
sem depender do Higor.

---

## Pendência conhecida — formato de metadados (justificar formalmente)
O `metadata_service.py` hoje grava chunk no formato legado (`node_id`, `shard_id`,
`chunk_path`). O novo `ChunkPlacement` do `.proto` usa `chunk_id`, `chunk_index`,
`size_bytes`, `replicas[]`. O `ListFiles` atual ainda lê `nodes_used` do formato
legado. Quando o `ConfirmUpload` migrar para gravar no formato novo, essa origem
muda. Decisão de design a justificar no relatório (o Prof. cobra justificativa).

---

## Decisão sobre CHUNK_SIZE (pendência P2 do ARQUITETURA.md)
Hoje `config.py` tem `CHUNK_SIZE = 64 KB`. No modelo gateway há duas granularidades:
- **chunk oficial do DFS** (unidade de placement e replicação) — sugerido: 4 MB
- **pedaço de transporte do stream** (quanto a CLI manda por mensagem gRPC) — pode ficar 64 KB

Ainda não decidido. Fechar com o Higor antes de implementar o ingress (afeta
`RequestUpload`/`ConfirmUpload`, não as RPCs anteriores).

---

## Limpeza de repositório
- `.gitignore` na raiz `DFS_M3/` ignorando `__pycache__/`, `*.pyc`, `data/`, `.venv/`, etc.
- `dfs/pb/dfs.proto.copy` — backup manual; pode apagar (o git já versiona o `.proto`).
- `teste_grpc.py` (raiz `Marco3/`) — teste manual do PLANO DE DADOS, do Higor (`DFSServiceStub`, fala com um nó na 9101). Deixar onde está; provável destino é ser apagado ao FIM da migração. Não mexer unilateralmente.

---

## Convenções de código — SEMPRE seguir

- **Comentários em português** em todo código Python
- **Sem abstrações desnecessárias** — código direto e legível
- **Nomes de variáveis descritivos** — sem abreviações obscuras
- **Não reescrever do zero** — migrar o que existe, preservando estrutura de pastas
- **Não alterar** `dfs/pb/dfs.proto`, `dfs_pb2.py`, `dfs_pb2_grpc.py`, `placement.py` sem combinar com o Higor
- **Não mexer em arquivos do Higor** (`storage_node.py`, `teste_grpc.py`, `run_cluster.py`, `start_coordinator.py`) sem combinar — no máximo avisar num commit pequeno

---

## Portas e endereços

| Componente   | Endereço           |
|--------------|--------------------|
| Coordenador  | 127.0.0.1:9100     |
| node1        | 127.0.0.1:9101     |
| node2        | 127.0.0.1:9102     |
| node3        | 127.0.0.1:9103     |
| node4        | 127.0.0.1:9104     |
| node5        | 127.0.0.1:9105     |

---

## Como recompilar os stubs gRPC (quando o .proto mudar)

```bash
cd DFS_M3
python -m grpc_tools.protoc -I=. --python_out=. --grpc_python_out=. dfs/pb/dfs.proto
```

Confirmar depois:
```bash
grep "import dfs_pb2" dfs/pb/dfs_pb2_grpc.py
# esperado: from dfs.pb import dfs_pb2 as dfs__pb2
```

---

## O que NÃO fazer

- Não criar `dfs/coordenador/` — a arquitetura é por camada; o servicer vai em `interface/server.py`
- Não mover `run_cluster.py`/`run_cli.py` para dentro de `DFS_M3/` — quebra o path do `run_cluster.py`
- Não implementar MapReduce/WordCount — marcado `[Não Feito]`, fora do escopo
- Não deixar o coordenador tocar em bytes de arquivos (a violação atual no `file_service._put` precisa ser removida, não replicada)
- Não alterar `dfs_pb2.py` ou `dfs_pb2_grpc.py` diretamente
- Não mudar a regra de placement sem combinar com o Higor
- Não apagar a pasta `data/` sem avisar — contém dados de testes