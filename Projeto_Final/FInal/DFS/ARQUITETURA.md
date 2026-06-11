# ARQUITETURA.md - Sistema de Arquivos Distribuído (DFS) - Marco 3

> Documento de arquitetura do projeto. Descreve o sistema **como ele está> implementado** após a integração dos planos de controle e de dados.\
> Mudanças no `.proto`, no `placement.py` ou neste documento exigem combinação prévia entre a dupla e entram na `main` via PR.

Disciplina: Sistemas Distribuídos 1\
Prof. Vagner José Sacramento Rodrigues\
Engenharia de Computação - EMC/UFG\
Autoria: Vitória Mendonça e Higor Ferreira Silva.

---

## 1. Visão geral

O DFS é um sistema de arquivos distribuído com **coordenação centralizada e dados distribuídos**, seguindo a separação rigorosa entre **plano de controle** e **plano de dados**.\
Essa separação é a decisão arquitetural central do projeto e organiza todo o resto.

- **1 coordenador** que centraliza o CONTROLE: metadados, decisões de posicionamento e supervisão dos nós. **Nunca toca nos bytes** dos arquivos do usuário.
- **5 nós de armazenamento** que centralizam os DADOS: armazenam chunks, replicam entre si e servem como porta de entrada/saída (*gateway*) quando designados.
- **1+ clientes (CLI)**: lê/grava o disco local e conversa com o coordenador (controle) e com um nó (dados) por operação. Não conhece a topologia do cluster nem decide nada sobre posicionamento.

**Parâmetros do cluster:** N = 5 nós, fator de replicação R = 3.

O fluxo de dados pesado (bytes de arquivos) flui **diretamente** entre a CLI e os nós, sem passar pelo coordenador. O coordenador só troca metadados leves. Esse é o ganho central da arquitetura: o coordenador não é gargalo de banda.

---

## 2. Princípio organizador: control plane vs data plane

A decisão fundamental do projeto é separar **quem decide** de **quem transporta**.

| | Plano de Controle | Plano de Dados |
|---|---|---|
| **Quem implementa** | Coordenador | Nós de armazenamento |
| **O que trafega** | Metadados leves (kB) | Bytes de arquivos (KB-GB) |
| **Quem usa** | CLI (controle) e nós (registro/heartbeat) | CLI (PUT/GET) e nós entre si (replicação) |
| **Padrão de RPC** | Unário | Streaming |

Essa divisão é também a divisão de trabalho da dupla: o plano de controle foi responsabilidade da Vitória, o plano de dados do Higor, e a fronteira entre eles é o contrato `.proto`. Os dois lados só se comunicam através de: o `.proto`, a regra de placement (`placement.py`) e os identificadores (`upload_id`, `download_id`, `chunk_id`).

**Justificativa formal:** em um DFS, o tráfego de dados é ordens de magnitude maior que o de controle. Se o coordenador intermediasse os bytes (modelo proxy), ele se tornaria o gargalo de toda a banda do cluster, e adicionar nós não aumentaria a vazão. Ao remover o coordenador do caminho dos dados, a vazão agregada cresce com o número de nós: cada novo nó adiciona banda de I/O. É o mesmo princípio do GFS e do HDFS, em que o NameNode/master serve metadados e os DataNodes servem dados.

---

## 3. Componentes

### Cliente (CLI)
Processo Python no terminal do usuário. Lê arquivos do disco local e os envia ao sistema; recebe e grava. É um **cliente fraco**: não fatia em chunks, não decide posicionamento, não conhece a topologia. Por operação, ele faz duas conversas: uma com o coordenado (controle) e uma com um nó (dados). Mantém um canal persistente com o coordenador durante a sessão interativa para evitar o custo de reabrir conexão a cada comando.

### Coordenador
Processo Python único. Hospeda o `ControlService`. Mantém:
- o **catálogo de metadados** (quais arquivos existem, em quantos chunks, timestamps e em quais nós está cada réplica), persistido em JSON;
- o **registro de nós** (membership canônica estática + estado vivo dinâmico via heartbeat).

Decide o posicionamento dos chunks, designa qual nó atua como ingress/egress por operação, e comanda a deleção física. Nunca toca nos bytes dos arquivos.

### Nós de armazenamento
Cinco processos independentes, cada um com porta e diretório próprios. Cada nó hospeda **três serviços gRPC na mesma porta**:
- `DataService`: atende a CLI no PUT (como ingress) e no GET (como egress);
- `ReplicationService`: atende outros nós (fan-out de réplicas, busca em peers) e o coordenador (deleção de chunks);
- `DataPlaneService`: recebe da CLI o plano de chunks antes do stream (ver §6).

Em background, cada nó também roda como **cliente** do coordenador: registra-se ao subir e envia heartbeat periódico.

---

## 4. Camadas de comunicação (serviços gRPC)

O contrato vive em dois arquivos `.proto`, ambos fonte de verdade:

- `dfs/pb/dfs.proto`: contrato compartilhado: os três serviços principais.
- `dfs/pb/dataplane.proto`: contrato interno do plano de dados (handoff do plano de chunks da CLI para o nó; ver §6). Separado de propósito para não alterar o `dfs.proto` nem nos stubs do coordenador.

| Serviço | Implementado por | Clientes | Tráfego | Padrão |
|---|---|---|---|---|
| **ControlService** | Coordenador | CLI e nós | pequeno (kB) | unário |
| **DataService** | Nós | CLI | grande (MB–GB) | streaming |
| **ReplicationService** | Nós | outros nós e coordenador | grande (MB) | streaming |
| **DataPlaneService** | Nós | CLI | pequeno (kB) | unário |

### RPCs do ControlService (7)
- `RegisterNode`: um nó se anuncia ao subir.
- `Heartbeat`: batimento periódico com block report (inventário de chunks).
- `RequestUpload`: autoriza um PUT: escolhe ingress, gera `upload_id`, devolve o mapa de chunks pré-computado.
- `ConfirmUpload`: o ingress confirma o que gravou; o coordenador grava os metadados.
- `RequestDownload`: autoriza um GET: escolhe egress por localidade, devolve o mapa de chunks.
- `DeleteFile`: comanda a deleção física dos chunks nos nós e remove os metadados.
- `ListFiles`: lista os arquivos conhecidos.

### RPCs do DataService (2)
- `UploadFile` (client-streaming): a CLI envia os bytes ao ingress.
- `DownloadFile` (server-streaming): o egress devolve os bytes à CLI.

### RPCs do ReplicationService (4)
- `StoreChunk` (client-streaming): o ingress envia um chunk a uma réplica.
- `FetchChunk` (server-streaming): o egress busca um chunk num peer.
- `DeleteChunk` (unário): o coordenador manda apagar um chunk.
- `ListChunks` (unário): diagnóstico / validação cruzada.

### RPCs do DataPlaneService (2)
- `SetUploadPlan` / `SetDownloadPlan` (unário): a CLI entrega ao nó o plano de chunks antes de abrir o stream (ver §6).

---

## 5. Organização do código (por camada)

A estrutura é organizada por **papel técnico**, não por componente. O servicer (adaptador gRPC, em `interface/`) delega para um serviço de lógica (em `application/`), que usa a infraestrutura de cluster (em `cluster/`) e a persistência física (em `storage/`).

```text
DFS/
├── dfs/
│   ├── interface/              # adaptadores gRPC + pontos de entrada de processo
│   │   ├── server.py           # COORDENADOR: ControlServiceServicer
│   │   ├── storage_node.py     # NÓ: hospeda Data + Replication + DataPlane; heartbeat
│   │   └── cli.py              # cliente de linha de comando (fluxo de duas chamadas)
│   ├── application/            # lógica de negócio (sem detalhe de rede)
│   │   ├── metadata_service.py # índice de arquivos armazenado em JSON
│   │   ├── data_service.py     # DataServicer: ingress (PUT) e egress (GET)
│   │   └── replication_service.py # ReplicationServicer: CRUD de chunks no disco
│   ├── cluster/                # infraestrutura de cluster compartilhada
│   │   ├── node_registry.py    # membership canônica + estado vivo (heartbeat)
│   │   ├── placement.py        # round-robin determinístico (fonte de verdade)
│   │   ├── plan_store.py       # PlanStore + DataPlaneServicer (handoff do plano)
│   │   ├── control_client.py   # cliente do ControlService (usado por nó e CLI)
│   │   └── replication_client.py # cliente do ReplicationService (coordenador e nós)
│   ├── storage/
│   │   └── local_storage.py    # persistência física: API por caminho e por chunk_id
│   ├── pb/                     # contratos e stubs gerados
│   │   ├── dfs.proto / dataplane.proto
│   │   └── *_pb2.py / *_pb2_grpc.py   (gerados, não editar à mão)
│   ├── client.py               # DataClient (cliente do nó-gateway) + reexporta ControlClient
│   └── config.py               # N, R, portas, CHUNK_SIZE, STREAM_SIZE, heartbeat
├── scripts/start_coordinator.py
├── tests/                      # testes manuais e mocks
├── ARQUITETURA.md
└── (em Final/) run_cluster.py, run_cli.py
```

---

## 6. Decisões de arquitetura (justificadas)

Esta seção reúne as decisões de design e o porquê de cada uma.

### 6.1 Modelo gateway: ingress no PUT, egress no GET
O coordenador não toca em bytes. No PUT, a CLI envia o arquivo inteiro, em stream, para **um** nó (o *ingress*), que fatia em chunks e replica para as demais réplicas. No GET, a CLI pede o arquivo a **um** nó (o *egress*), que junta os chunks (locais + buscados em peers) e devolve em ordem.

**Justificativa:** concentra o trabalho de fatiar/remontar num nó do cluster (que tem banda e está próximo dos dados), não no cliente nem no coordenador.

### 6.2 Placement: round-robin determinístico por índice de chunk
As réplicas de cada chunk são dadas por uma regra pura, sem estado:

```
réplicas do chunk i = [ N[(i+0) % N], N[(i+1) % N], ..., N[(i+R-1) % N] ]
```

A primeira réplica é o *primary*. Com N=5 e R=3, o chunk 0 vai para [node1, node2, node3], o chunk 1 para [node2, node3, node4], e assim por diante.

**Justificativa:** a regra é **determinística**, então qualquer componente calcula o posicionamento de um chunk só a partir do seu índice, sem consultar tabela. Isso substitui o sharding por hash do Marco 2. A distribuição é uniforme: num arquivo de 10 chunks em 5 nós, cada nó recebe 6 chunks. Comparado ao hash, o round-robin garante espalhamento uniforme exato (o hash só garante uniformidade estatística) e produz uma sequência previsível, fácil de auditar na defesa.

**Invariante crítica:** o placement recebe SEMPRE a **membership canônica** (os 5 nós, na ordem fixa), nunca a lista de nós vivos. Se um nó cair e a lista virar 4, o `% N` muda e todos os chunks já gravados deixariam de ser encontrados. Liveness afeta de qual réplica se *lê* / para onde se *re-replica*, nunca a fórmula. A função `replicas_for_chunk` recebe `cluster_size` e falha alto se a lista
divergir (blindagem contra esse erro).

### 6.3 Placement é decidido no write e persistido (elasticidade)
`replicas_for_chunk` é chamada **uma vez por chunk**, no `RequestUpload`, com a membership canônica daquele momento. O resultado vira `ChunkPlacement.replicas` nos metadados e é **imutável**. Nenhuma operação posterior (GET, DELETE) recalcula posicionamento (todas leem dos metadados).

**Justificativa (elasticidade):** quando um nó novo entra (via `RegisterNode`), ele passa a integrar a membership canônica. Operação O(1) no coordenador, **zero movimentação de dados existentes**. Uploads futuros já incluem o nó novo; uploads antigos permanecem onde estão, porque seus metadados não mudam. Recalcular posicionamento a cada mudança de membership exigiria mover dados em massa (o modelo "decide no write, metadados são a verdade" evita isso).

### 6.4 Ingress por round-robin entre arquivos
O ingress de cada arquivo é escolhido por `ingress_for_file`, um round-robin **entre arquivos** (um contador no coordenador rotaciona o nó a cada novo upload). O ingress é escolhido entre os nós **vivos**.

**Justificativa:** distribui ao longo do tempo a carga de "ser o nó que recebe o upload" entre todos os nós, evitando que um único nó vire gargalo permanente de ingestão. É um papel de **transporte** (do arquivo inteiro), ortogonal ao papel de **réplica** (por chunk): o ingress pode não ser réplica de nenhum chunk daquele arquivo, ele só repassa os bytes. Optou-se por round-robin em vez de escolha por
carga (`active_uploads`) porque a carga instantânea é uma métrica fraca neste cenário (ela muda durante a própria operação); o round-robin ataca o gargalo estrutural de forma principiada e é defensável no relatório.

### 6.5 Egress por localidade
No GET, o egress é o nó **vivo** que guarda o maior número de chunks do arquivo. Empate é resolvido de forma determinística (menor índice).

**Justificativa (localidade de dados):** o egress monta o arquivo para devolver à CLI. Quanto mais chunks ele já tem localmente, menos precisa buscar em peers (via `FetchChunk`), logo há menos tráfego entre nós e o download é mais rápido. É o mesmo princípio de localidade do MapReduce: leve a computação para perto dos dados.
O desempate por carga (`active_downloads`) foi deixado como refinamento futuro, pela mesma razão do ingress (carga instantânea é métrica fraca).

### 6.6 Confirmação do upload parte do ingress, não da CLI
Quem chama `ConfirmUpload` no coordenador é o ingress, depois de replicar, não a CLI.

**Justificativa:** o ingress é quem **sabe** o que conseguiu de fato gravar e replicar. Confirmar a partir dele dá a informação correta ao coordenador e tira essa responsabilidade do cliente fraco. O arquivo só passa a existir para o sistema (aparece no LIST, é encontrável no GET) **após** o `ConfirmUpload`: antes disso o upload é apenas pendente.

### 6.7 Duas granularidades de tamanho: CHUNK_SIZE e STREAM_SIZE
- `CHUNK_SIZE = 4 MB` (**chunknização**): unidade de posicionamento e replicação. Define em quantos chunks o arquivo é cortado e quantas entradas de metadado e rodadas de replicação ele gera.
- `STREAM_SIZE = 64 KB` (o **pedaço de transporte** do stream gRPC): quanto a CLI envia por mensagem. Não é unidade de posicionamento, só de transporte.

**Justificativa:** as duas têm forças opostas. O transporte quer pedaços **pequenos** (baixo uso de memória, bem abaixo do limite de mensagem do gRPC). O chunk oficial quer ser **grande** por duas razões convergentes: (1) **overhead de metadados** - com 64 KB, um arquivo de 256 MB geraria 4096 chunks; com 4 MB, apenas 64 (redução de 64×), o que responde diretamente ao feedback do Marco 2 sobre escalar o tamanho do chunk; (2) **paralelismo de distribuição** - chunks grandes demais concentrariam o arquivo em poucos nós e sabotariam o balanceamento, que é o foco do Marco 3. 4 MB equilibra as duas forças.

### 6.8 Handoff do plano de chunks (dataplane.proto)
**Decisão tomada na integração.** No `dfs.proto`, o nó recebe da CLI apenas o token da operação (`upload_id` no PUT, `download_id` no GET). Mas o ingress precisa saber **em quais réplicas** gravar cada chunk, e o egress precisa saber **quais chunks** compõem o arquivo e **onde** estão. Essa informação (o `repeated ChunkPlacement`) o coordenador devolve à **CLI**, não ao nó.

A solução foi um serviço interno do plano de dados, o `DataPlaneService` (`SetUploadPlan`/`SetDownloadPlan`), que a CLI chama **antes** de abrir o stream para entregar o plano ao nó. O nó guarda o plano em memória (`PlanStore`, indexado pelo id) e o consome durante o stream.

**Justificativa:** o handoff do plano é uma conversa interna do plano de dados (CLI ↔ nó), não do plano de controle. Mantê-lo num `.proto` separado preserva o `dfs.proto` e os stubs do coordenador intocados, então quem calcula o posicionamento continua sendo o coordenador, quem grava o índice continua sendo o `ConfirmUpload`. A CLI só **transporta** o plano. A alternativa (passar o plano na primeira mensagem do stream `UploadFile`) misturaria metadado de controle com o stream de bytes; separar é mais limpo.

### 6.9 Deleção comandada pelo coordenador, em paralelo
O `DeleteFile` é conduzido pelo coordenador, não pela CLI. O coordenador lê os metadados, inverte o mapa de "chunk → réplicas" para "nó → seus chunks", e dispara a deleção em paralelo: **um nó por thread, um canal por nó**. A ordem é **chunks primeiro, metadados depois**.

**Justificativa (quem comanda):** a CLI é cliente fraco e não conhece a topologia (quais chunks, em quais nós). Esse mapa vive nos metadados, que são do coordenador. Além disso, o coordenador é a **autoridade** sobre "este arquivo existe"; remover chunks e remover metadados precisam ser conduzidos pelo mesmo ator. Apagar é uma decisão de **controle** (muda o estado do sistema), por isso passa pelo coordenador, ao contrário do PUT/GET, em que os bytes vão direto.

**Justificativa (ordem):** se os metadados fossem apagados primeiro e o processo morresse no meio, os chunks ficariam órfãos no disco sem registro de que existem (lixo invisível). No sentido inverso, o pior caso é um metadado apontando para chunk já apagado, o que o GET detecta. Prefere-se o erro detectável ao silencioso.

**Justificativa (paralelo):** percorrer chunk a chunk em série seria lento num arquivo grande (milhares de chamadas em fila). Agrupando por nó e disparando uma thread por nó (cada uma reusando um único canal), os nós apagam ao mesmo tempo, o mesmo padrão de fan-out usado na replicação do PUT.

A deleção é *best-effort*: se um nó está morto, seus chunks não são apagados agora e contam como falha, mas o arquivo some dos metadados mesmo assim. Os chunks órfãos seriam limpos quando o nó voltar (mecanismo `chunks_to_delete` do heartbeat, com campo já no contrato e lógica planejada para o Marco 4).

### 6.10 Quórum de escrita W=2
No fan-out do PUT, o ingress só considera um chunk gravado com sucesso se pelo menos **W=2** réplicas (contando a si próprio, se for réplica) confirmarem.

**Justificativa:** com R=3 réplicas, exigir W=2 confirmações garante que a escrita sobreviva à falha de uma réplica durante o upload, sem exigir que todas as trêsestejam no ar (o que tornaria o sistema frágil a qualquer falha). É a base da inequação de quórum W + R > N para consistência forte (ver §9 sobre o estado atual da leitura).

### 6.11 Identidade vs. estado vivo no registro de nós
O `NodeRegistry` separa duas responsabilidades:
- **membership canônica** (estática, lida do `config.py`): a lista fixa dos N nós, sempre na mesma ordem. É o que o placement consome.
- **estado vivo** (dinâmico): quem está ligado agora, via `register_node` e `record_heartbeat`. Classifica cada nó como ALIVE / SUSPECT / DEAD pelo tempo desde o último batimento.

**Justificativa:** posicionamento (decidir onde gravar) usa a canônica e inclui nós temporariamente fora do ar, pois um nó em manutenção ainda deve receber sua cota de chunks (a re-replicação entrega depois). Roteamento pontual (escolher ingress/egress) usa só os vivos, já que não dá para mandar um cliente falar com um nó morto. Misturar as duas listas quebraria o determinismo do placement.

---

## 7. Fluxo das operações

### PUT
1. **CLI → Coordenador** `RequestUpload(logical_path, total_size)`. O coordenador escolhe o ingress (vivo, round-robin), pré-computa os `ChunkPlacement` com a membership canônica, gera `upload_id` e responde com (`upload_id`, ingress, mapa de chunks).
2. **CLI → Ingress** `SetUploadPlan(upload_id, total, chunks)`: entrega o plano.
3. **CLI → Ingress** `UploadFile` (stream de bytes em pedaços de `STREAM_SIZE`).
4. **Ingress** re-agrupa os bytes em chunks de `CHUNK_SIZE`; para cada chunk, grava localmente se for réplica e faz fan-out paralelo (`StoreChunk`) para as demais réplicas; valida o quórum W=2.
5. **Ingress → Coordenador** `ConfirmUpload(upload_id, chunks, total)`. O coordenador grava os metadados.
6. **Ingress → CLI** `UploadResult` (fim do stream).

### GET
1. **CLI → Coordenador** `RequestDownload(logical_path)`. O coordenador lê os metadados (não recalcula posicionamento), escolhe o egress por localidade, gera `download_id` e responde com (`download_id`, egress, total, mapa de chunks).
2. **CLI → Egress** `SetDownloadPlan(download_id, total, chunks)`: entrega o plano.
3. **CLI → Egress** `DownloadFile(download_id)` (stream de bytes).
4. **Egress** monta o arquivo: lê os chunks que tem localmente, busca os demais em peers (`FetchChunk`), e emite o stream em ordem.

### DELETE
1. **CLI → Coordenador** `DeleteFile(logical_path)`.
2. Coordenador inverte o mapa para "nó → chunks", dispara `DeleteChunk` em paralelo (um nó por thread), remove os metadados, responde.

### LIST
1. **CLI → Coordenador** `ListFiles()`. Coordenador devolve a lista dos metadados.

---

## 8. Heartbeat e detecção de falhas

- Cada nó registra-se ao subir (`RegisterNode`) e envia heartbeat a cada `HEARTBEAT_INTERVAL` (2 s), com: `node_id`, espaço livre, uploads/downloads ativos e o **block report** (inventário de chunks que possui).
- A classificação é calculada na hora da consulta, sem thread de fundo, pelo tempo de silêncio desde o último batimento:
  - silêncio < `HEARTBEAT_SUSPECT` (4 s) → **ALIVE**;
  - entre 4 s e `HEARTBEAT_DEAD` (8 s) → **SUSPECT**;
  - ≥ 8 s → **DEAD**.

**Justificativa dos tempos:** os limiares são múltiplos do intervalo, tolerando algumas perdas de batimento antes de reagir (≈2 batimentos para SUSPECT, ≈4 para DEAD). Os valores 2/4/8 priorizam **detecção rápida** num ambiente de loopback (rede local confiável), onde o risco de falso positivo por atraso de rede é baixo.
A janela SUSPECT existe para dar uma margem antes de declarar morte definitiva.

No Marco 3, o coordenador **usa** o status para roteamento (remove nós mortos da escolha de ingress/egress) e **guarda** o block report, mas a **re-replicação automática** (restaurar R=3 copiando chunks de um nó morto para sobreviventes) fica para o Marco 4. O campo `chunks_to_delete` do heartbeat já está no contrato para a limpeza de órfãos do Marco 4.

---

## 9. Consistência e tolerância a falhas (estado atual e limites)

- **Escrita (implementado):** quórum W=2 no fan-out do PUT. Um chunk só é dado como gravado se 2 das 3 réplicas confirmarem.
- **Leitura (implementado):** o egress monta o arquivo a partir das réplicas, com **failover sequencial**: se um peer não tem/não responde um chunk, tenta o próximo da lista de réplicas daquele chunk. Isso garante disponibilidade da leitura enquanto **pelo menos uma** réplica de cada chunk estiver viva.
- **Falha de nó durante operação (Marco 3):** o nó morto sai de `alive_members` (não é mais escolhido como ingress/egress) mas permanece na membership canônica. Chunks que o tinham operam com R−1 réplicas vivas durante a falha.
- **Re-replicação automática (Marco 4):** restaurar R=3 após uma morte definitiva ainda não é feito; é o foco do próximo marco.
- **Versionamento / quórum de leitura R=2 com anti-entropia:** descrito como objetivo de design, mas o código atual faz failover sequencial, não um quórum R=2 com comparação de versão. A inequação W + R > N (com R=2) e o versionamento por carimbo são o caminho para consistência forte linearizável, planejado como evolução.

**Trade-off (CAP):** o sistema prioriza tolerância a partição e segurança da escrita (quórum W). Em caso de não atingir o quórum de escrita, a operação falha em vez de gravar de forma inconsistente (preferência por consistência sobre disponibilidade de escrita no quadrante CP.

---

## 10. Parâmetros do cluster (config.py)

Todos os valores são lidos de `dfs/config.py`.

| Parâmetro | Valor | Observação |
|---|---|---|
| N (nós) - `NODE_COUNT` | 5 | menor N com graus de liberdade reais de distribuição |
| R (replicação) - `REPLICATION_FACTOR` | 3 | tolera perda de 2 réplicas; permite quórum |
| `CHUNK_SIZE` | 4 MB (`4 * 1024 * 1024`) | chunk oficial: unidade de placement/replicação |
| `STREAM_SIZE` | 64 KB (`64 * 1024`) | pedaço de transporte do stream gRPC |
| Porta do coordenador - `PORT` | 9100 | `127.0.0.1:9100` |
| Porta base dos nós - `BASE_NODE_PORT` | 9101 | node1→9101, …, node5→9105 |
| `HEARTBEAT_INTERVAL` | 2 s | intervalo esperado entre batimentos |
| `HEARTBEAT_SUSPECT` | 4 s | silêncio a partir do qual o nó vira SUSPECT |
| `HEARTBEAT_DEAD` | 8 s | silêncio a partir do qual o nó vira DEAD |
| `DATA_DIR` | `BASE_DIR/data` | nós em `data/nodes/nodeN/`, metadados em `data/metadata/` |

**Justificativa N=5, R=3:** R=3 é o menor fator que tolera a perda de 2 réplicas e
viabiliza quórum (W + R > N). N=5 é o menor número de nós que dá graus de liberdade
combinatórios reais ao round-robin — com N=3 e R=3, `min(R,N)=3` colocaria todo
chunk em todos os nós, e não haveria o que balancear, justamente o foco do Marco 3.

---

## 11. Geração dos stubs gRPC

Compilar SEMPRE a partir da raiz do projeto (`DFS/`), com `-I=.`, para os dois `.proto`:

```bash
cd DFS
python -m grpc_tools.protoc -I=. --python_out=. --grpc_python_out=. dfs/pb/dfs.proto
python -m grpc_tools.protoc -I=. --python_out=. --grpc_python_out=. dfs/pb/dataplane.proto
```

O `-I=.` faz o protoc enxergar o caminho como `dfs/pb/dfs.proto` e gerar o import qualificado `from dfs.pb import dfs_pb2` (o `dataplane_pb2` reusa `dfs_pb2` por esse mesmo import). Com `-I=dfs/pb` ele geraria `import dfs_pb2` (plano), que quebra com `ModuleNotFoundError`. Nunca editar os arquivos gerados à mão.

---

## 12. Execução

A partir de `Final/`:

```bash
# sobe o cluster inteiro (5 nós + coordenador)
python run_cluster.py

# em outro terminal, usa a CLI
python run_cli.py put <arquivo_local> <caminho_logico>
python run_cli.py get <caminho_logico> <saida_local>
python run_cli.py list
python run_cli.py rm <caminho_logico>
python run_cli.py            # modo interativo
```

Arquivos que importam `dfs` e rodam de dentro de `DFS/` usam modo módulo
(`python -m ...`), para o `import dfs` resolver a partir da raiz correta.

> Nota operacional: o `run_cluster.py` sobe os nós antes do coordenador, então os primeiros batimentos podem falhar com "coordenador no ar?" até o coordenador subir. O heartbeat tem retry, então os nós entram como ALIVE no ciclo seguinte, o aviso inicial é esperado e inofensivo.

---

## 13. Divisão de trabalho

| Plano | Responsável | Serviços / componentes |
|---|---|---|
| Controle | Vitória Mendonça | ControlService (coordenador), NodeRegistry, placement, metadata, replication_client (deleção) |
| Dados | Higor Ferreira Silva | DataService + ReplicationService + DataPlaneService (nós), PlanStore, LocalStorage, CLI de dados |

A fronteira entre os planos é o contrato `.proto`, a regra de placement e os identificadores (`upload_id`, `download_id`, `chunk_id`). Cada lado foi desenvolvido e testado isoladamente (o controle com um mock de nó; o dado com um mock de coordenador) antes da integração.

---

## 14. Evolução planejada

- **Marco 4 (tolerância a falhas):** re-replicação automática ao detectar nó DEAD; limpeza de chunks órfãos via `chunks_to_delete`; possível eleição de líder para o coordenador deixar de ser ponto único de falha; versionamento e quórum de leitura R=2 com anti-entropia para consistência forte linearizável.
- **Marco 5 (escalabilidade):** validação empírica de `CHUNK_SIZE` por benchmark; testes de carga e métricas; remoção planejada de nó (decommission).