# CLAUDE.md — DFS Marco 3 (Projeto Final Sistemas Distribuídos)

> Instruções para o Claude Code sobre o estado atual do projeto.
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

## Estado atual do código — O QUE JÁ EXISTE

### Fundação (pronta e estável — não alterar sem combinar com o Higor)
- `dfs/pb/dfs.proto` — contrato dos 3 serviços gRPC completo e compilado
- `dfs/pb/dfs_pb2.py` e `dfs_pb2_grpc.py` — stubs gerados (NUNCA editar à mão)
- `dfs/cluster/placement.py` — regra round-robin determinística implementada e comentada
- `dfs/config.py` — `NODE_COUNT = 5`, portas 9100 (coordenador) + 9101–9105 (nós)
- `ARQUITETURA.md` — decisões de design documentadas

### Camada de aplicação (código legado do Marco 2, em migração para a nova arquitetura)
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
- `dfs/interface/server.py` — expõe `DFSServiceServicer` (legado); precisa expor `ControlServiceServicer`
- `dfs/interface/storage_node.py` — expõe `DFSServiceServicer` (legado); precisa expor `DataServiceServicer` + `ReplicationServiceServicer`
- `dfs/interface/cli.py` — funcional com o modelo atual
- `dfs/storage/local_storage.py` — funcional, sem mudanças previstas

### Infraestrutura
- `run_cluster.py` — sobe coordenador + N nós dinamicamente via `NODE_ORDER`
- `run_cli.py` — ponto de entrada da CLI
- `scripts/start_coordinator.py` — script auxiliar

---

## O que está pendente (próximos passos — branch da Vitória)

### Prioridade 1 — Migrar o coordenador para o novo `.proto`
O `server.py` e `file_service.py` ainda expõem `DFSServiceServicer`. A migração é:

1. Criar `dfs/coordenador/control_service.py` implementando `ControlServiceServicer` com:
   - `RegisterNode` — adicionar nó ao registro dinâmico
   - `Heartbeat` — atualizar status, marcar SUSPECT/DEAD por timeout
   - `RequestUpload` — escolher ingress via `placement.ingress_for_file()`, gerar `upload_id`, responder
   - `ConfirmUpload` — receber do ingress a lista de chunks, persistir via `MetadataService`
   - `RequestDownload` — escolher egress por localidade, responder com `download_id`
   - `DeleteFile` — disparar `DeleteChunk` nos nós via `ReplicationService`, remover metadados
   - `ListFiles` — retornar lista do `MetadataService`

2. Atualizar `server.py` para registrar `ControlServiceServicer` no servidor gRPC

3. Atualizar `client.py` para usar `ControlServiceStub` em vez de `DFSServiceStub`

4. Atualizar `cli.py` para o novo fluxo de duas chamadas (coordenador + nó gateway)

### Prioridade 2 — NodeRegistry dinâmico
O `NodeRegistry` atual é estático (lê de `config.py`). Precisa aceitar `RegisterNode` e atualizar status via heartbeat. Pode manter o config como bootstrap inicial.

### Prioridade 3 — Heartbeat com detecção de falhas
- Nós enviam heartbeat a cada 2 s com `chunk_ids` (block report)
- Coordenador marca SUSPECT após 6 s sem heartbeat, DEAD após 15 s
- Re-replicação automática fica para o marco de tolerância a falhas — o campo já está no contrato

### Prioridade 4 — Mock de nó para testes isolados
Criar `tests/mocks/mock_node.py` que implementa `DataServiceServicer` e `ReplicationServiceServicer` com respostas fixas, para testar o plano de controle sem depender do Higor.

---

## Decisão sobre CHUNK_SIZE (pendência P2)
Hoje `config.py` tem `CHUNK_SIZE = 64 KB`. No modelo gateway há duas granularidades:
- **chunk oficial do DFS** (unidade de placement e replicação) — sugerido: 4 MB
- **pedaço de transporte do stream** (quanto a CLI manda por mensagem gRPC) — pode ficar 64 KB

Ainda não decidido. Fechar com o Higor antes de implementar o ingress.

---

## Convenções de código — SEMPRE seguir

- **Comentários em português** em todo código Python
- **Sem abstrações desnecessárias** — código direto e legível
- **Nomes de variáveis descritivos** — sem abreviações obscuras
- **Não reescrever do zero** — migrar o que existe, preservando estrutura de pastas
- **Não alterar** `dfs/pb/dfs.proto`, `dfs_pb2.py`, `dfs_pb2_grpc.py`, `placement.py` sem combinar com o Higor

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

- Não implementar MapReduce/WordCount — marcado `[Não Feito]`, fora do escopo
- Não deixar o coordenador tocar em bytes de arquivos (a violação atual no `file_service._put` precisa ser removida, não replicada)
- Não alterar `dfs_pb2.py` ou `dfs_pb2_grpc.py` diretamente
- Não mudar a regra de placement sem combinar com o Higor
- Não apagar a pasta `data/` sem avisar — contém dados de testes