# Distributed File System (DFS) — Marco 3
## Manual de Arquitetura, Engenharia de Sistemas Distribuídos e Documentação Técnica Operacional

> Disciplina: Sistemas Distribuídos 1 — Prof. Vagner José Sacramento Rodrigues
> Engenharia de Computação, EMC/UFG — Autoria: Vitória Mendonça e Higor Ferreira Silva.
>
> Este documento descreve o sistema **como ele está implementado** na branch `main`,
> após a integração dos planos de controle e de dados. Mudanças no `.proto`, no
> `placement.py` ou na arquitetura exigem combinação prévia entre a dupla e entram
> na `main` via PR.

---

## 📌 1. Visão Geral

Este projeto consiste na implementação de um **Sistema de Arquivos Distribuído (DFS - Distributed File System)** desenvolvido integralmente em Python. O sistema evoluiu a partir de uma arquitetura legada (Marcos 1 e 2) baseada em sockets TCP brutos com *framing por tamanho* manual, e no **Marco 3** migra de forma definitiva para uma infraestrutura de rede moderna, multiplexada e baseada em **gRPC** (Remote Procedure Calls) sobre **HTTP/2**, utilizando **Protocol Buffers (Protobuf)** tanto como linguagem de definição de interface (IDL - Interface Definition Language) quanto como motor de serialização binária em tempo de execução.

A decisão arquitetural central do Marco 3 — e que organiza todo o resto do sistema — é a **separação rigorosa entre o plano de controle (control plane) e o plano de dados (data plane)**: o coordenador centraliza unicamente as decisões e os metadados, enquanto os bytes pesados dos arquivos trafegam **diretamente** entre o cliente e os nós (e entre os nós), sem nunca passar pelo coordenador. O cluster é estruturado sobre os seguintes pilares:

- **Coordenador único (Control Plane):** hospeda o `ControlService`. Mantém o catálogo de metadados e o registro de nós, decide o posicionamento dos chunks, designa quem atua como porta de entrada/saída por operação e comanda a deleção física. **Nunca toca nos bytes** dos arquivos do usuário.
- **Cinco nós de armazenamento (Data Plane):** $N=5$ processos independentes, cada um com porta e diretório próprios. Armazenam chunks, replicam entre si e servem como *gateway* (ingress no PUT, egress no GET) quando designados.
- **Cliente (CLI):** lê/grava o disco local e conversa, por operação, com o coordenador (controle) e com um nó (dados). É um **cliente fraco**: não fatia em chunks, não decide posicionamento, não conhece a topologia. Mantém um canal persistente com o coordenador durante a sessão interativa.
- **Fragmentação em chunks de tamanho fixo:** arquivos são cortados em blocos de `CHUNK_SIZE = 4 MB` por um **nó-gateway (ingress)**, e não pelo cliente nem pelo coordenador.
- **Replicação ativa com fator $R=3$:** cada chunk é propagado para três réplicas em nós distintos, via *fan-out* paralelo disparado pelo ingress.
- **Placement determinístico por round-robin:** a localização das réplicas de cada chunk é dada por uma regra pura sobre o índice do chunk (módulo o número de nós), calculada **uma única vez** no upload e persistida nos metadados, jamais recalculada.
- **Quórum de escrita $W=2$:** o ingress só dá um chunk por gravado quando ao menos duas das três réplicas confirmam.
- **Leitura por failover sequencial:** no GET, o egress monta o arquivo a partir dos chunks que tem localmente e busca os demais em peers, tentando as réplicas de cada chunk em ordem até obter o bloco — disponível enquanto ao menos uma réplica de cada chunk estiver viva.
- **Supervisão de nós por heartbeat:** cada nó registra-se ao subir e envia batimentos periódicos com inventário de chunks (*block report*); o coordenador classifica cada nó como ALIVE, SUSPECT ou DEAD e usa o status para roteamento.
- **Handoff de plano via contrato interno (`dataplane.proto`):** o cliente repassa ao nó-gateway o mapa de chunks recebido do coordenador **antes** de abrir o stream de bytes, mantendo o contrato compartilhado (`dfs.proto`) e os stubs do coordenador intocados.
- **Metadados persistentes em JSON:** o estado lógico do cluster é mantido num índice mestre (`metadata_index.json`), mapeando, por arquivo, o tamanho total, os chunks e, por chunk, suas réplicas.

**O Marco 3 está integrado e funcional de ponta a ponta:** as operações `put`, `get`, `list` e `rm` funcionam com verificação byte a byte do round-trip, com o coordenador real (`ControlService`) e os cinco nós reais cooperando via gRPC. O ganho central da arquitetura é que o coordenador não é gargalo de banda: como os bytes fluem direto entre cliente e nós, a vazão agregada cresce com o número de nós (mesmo princípio do NameNode/DataNodes do HDFS e do master/chunkservers do GFS).

---

## 🧠 2. Arquitetura Geral e Decomposição de Planos

O DFS organiza-se em camadas rigidamente isoladas de responsabilidade, por **papel técnico** (interface, aplicação, cluster, storage), não por componente. Um servicer gRPC (adaptador de rede, em `interface/`) delega para um serviço de lógica (em `application/`), que usa a infraestrutura de cluster (em `cluster/`) e a persistência física (em `storage/`).

### 2.1 O Plano de Controle (Control Plane) vs. O Plano de Dados (Data Plane)

A separação fundamental do Marco 3 distingue **quem decide** de **quem transporta**:

| | Plano de Controle | Plano de Dados |
|---|---|---|
| **Quem implementa** | Coordenador | Nós de armazenamento |
| **O que trafega** | Metadados leves (kB) | Bytes de arquivos (KB-GB) |
| **Quem usa** | CLI (controle) e nós (registro/heartbeat) | CLI (PUT/GET) e nós entre si (replicação) |
| **Padrão de RPC** | Unário | Streaming |

- **O Control Plane (Coordenador):** opera como um servidor gRPC leve. Quando a CLI solicita um `put`, o coordenador calcula o placement round-robin de cada chunk, escolhe o nó ingress, reserva um `upload_id` e devolve o mapa de `ChunkPlacement` (IDs e endereços `host:port` das réplicas). Ele só persiste os metadados quando o ingress confirma o upload (`ConfirmUpload`). Nunca abre buffers para os bytes do usuário.
- **O Data Plane (CLI e Workers):** de posse do plano, a CLI o entrega ao nó-gateway via `SetUploadPlan` e abre um stream direto com esse nó (ingress no PUT, egress no GET). Os dados trafegam ponto a ponto pela periferia, e a replicação entre réplicas e a busca em peers também ocorrem diretamente entre nós.

**Justificativa formal:** em um DFS, o tráfego de dados é ordens de magnitude maior que o de controle. Se o coordenador intermediasse os bytes (modelo proxy), tornar-se-ia o gargalo de toda a banda do cluster, e adicionar nós não aumentaria a vazão. Ao removê-lo do caminho dos dados, a vazão agregada cresce com o número de nós: cada novo nó adiciona banda de I/O.

### 2.2 Visão Detalhada dos Componentes

- **CLI Client:** intercepta as entradas do operador, faz parsing dos comandos e gerencia o ciclo de vida de um cliente persistente. Mantém um canal HTTP/2 aquecido com o coordenador durante toda a sessão interativa, evitando reabrir conexão a cada comando. Faz **três chamadas** no PUT (RequestUpload → SetUploadPlan → UploadFile) e três simétricas no GET.
- **Coordenador (ControlService):** servidor centralizado da inteligência de controle — posicionamento de chunks, escolha de ingress/egress, supervisão de nós via heartbeat e centralização do índice. Escuta em `127.0.0.1:9100`.
- **Placement Engine (`placement.py`):** módulo matemático puro que computa, de forma determinística e sem estado, as réplicas de cada chunk a partir do seu índice (round-robin módulo $N$). A mesma camada decide o ingress de cada arquivo (round-robin entre arquivos).
- **Node Registry (`node_registry.py`):** catálogo em memória que separa a **membership canônica** (lista fixa e ordenada dos $N$ nós, lida do `config.py`, consumida pelo placement) do **estado vivo** (quem está ALIVE / SUSPECT / DEAD agora, atualizado por registro e heartbeat, consumido pelo roteamento).
- **Control Service Servicer (`server.py`):** implementa concretamente as sete RPCs do plano de controle, usando o `NodeRegistry`, o `placement.py`, o `MetadataService` e o `replication_client`.
- **Metadata Service (`metadata_service.py`):** componente transacional que lê e escreve, sob lock, o `metadata_index.json`. Armazena, por arquivo, o tamanho total, a lista de chunks e, por chunk, as réplicas; mantém um bloco de distribuição com `chunk_count` e `nodes_used`.
- **Data Service (`data_service.py`):** servicer gRPC do nó. Implementa o `UploadFile` (nó como **ingress**: recebe o stream, reagrupa em chunks de `CHUNK_SIZE`, grava local se for réplica e dispara o fan-out paralelo) e o `DownloadFile` (nó como **egress**: junta chunks locais e busca os faltantes em peers, emitindo em ordem).
- **Replication Service (`replication_service.py`):** servicer gRPC que atende outros nós e o coordenador — `StoreChunk` (recebe um chunk de uma réplica), `FetchChunk` (serve um chunk a um peer), `DeleteChunk` (apaga a mando do coordenador) e `ListChunks` (diagnóstico).
- **Data Plane Service / PlanStore (`plan_store.py`):** serviço interno exposto pelos nós na mesma porta do DataService. Recebe da CLI o plano de chunks (`SetUploadPlan` / `SetDownloadPlan`) antes do stream e o guarda em memória (indexado por `upload_id`/`download_id`), para o DataService consumir durante a operação.
- **Control Client (`control_client.py`):** cliente do `ControlService`, usado pelo **nó** (`register`, `heartbeat`, `confirm_upload`) e pela **CLI** (`request_upload`, `request_download`, `delete_file`, `list_files`).
- **Replication Client (`replication_client.py`):** reúne dois clientes do `ReplicationService` — as funções de nível de coordenador (`delete_node_chunks`/`delete_one_chunk`, usadas no `DeleteFile`) e a classe `ReplicationClient` (usada pelos nós no fan-out do PUT e no failover do GET).
- **Local Storage (`local_storage.py`):** encapsula o I/O de disco. Grava cada chunk em `chunks/<chunk_id>` (sem extensão), com isolamento de caminhos contra *path traversal* e limpeza de subpastas vazias.
- **Data Client (`client.py`):** cliente gRPC do nó-gateway, com os stubs do `DataService` e do `DataPlaneService` na mesma conexão.

---

## 🔍 3. Diagramas de Fluxo e Arquitetura

As linhas pontilhadas simbolizam tráfego de controle e sinalização de metadados (CLI ↔ coordenador; nós → coordenador no registro/heartbeat); as linhas duplas contínuas simbolizam o transporte de payloads binários (CLI ↔ nó-gateway e nó ↔ nó).

### 3.1 Topologia de Rede e Fluxo Geral

```text
       ===================================================================
       |                     TOPOLOGIA LOGICA DO DFS                     |
       ===================================================================

                            +-------------------+
                            |    COORDENADOR    |
                            |  (Control Plane)  |
                            |  127.0.0.1:9100   |
                            +-------------------+
                              .       ^       .
        [RequestUpload/Download].     |       . [RegisterNode / Heartbeat]
        [ConfirmUpload/Delete]  .     |       . (nós -> coordenador, controle)
        (Sinalização/Metadados) .     |       .
                              v       |       .
                      +-------------------------------+
                      |          CLI CLIENT           |
                      |  (cliente fraco / Data Plane) |
                      +-------------------------------+
                          ||  (1) SetUploadPlan / SetDownloadPlan  (handoff)
                          ||  (2) UploadFile / DownloadFile (stream de bytes)
                          vv
                  +-----------------------------------------------+
                  |             NO-GATEWAY (ingress/egress)        |
                  +-----------------------------------------------+
                     ||  fan-out StoreChunk (PUT) / FetchChunk (GET)
                     ||  (trafego nó-a-nó, direto, sem coordenador)
          +-----------++-----------+-----------+-----------+
          v           v           v           v           v
   +-----------+ +-----------+ +-----------+ +-----------+ +-----------+
   | STORAGE 1 | | STORAGE 2 | | STORAGE 3 | | STORAGE 4 | | STORAGE 5 |
   |  :9101    | |  :9102    | |  :9103    | |  :9104    | |  :9105    |
   +-----------+ +-----------+ +-----------+ +-----------+ +-----------+
   | Disk 1    | | Disk 2    | | Disk 3    | | Disk 4    | | Disk 5    |
   +-----------+ +-----------+ +-----------+ +-----------+ +-----------+
```

### 3.2 Sequência da Operação PUT

Os bytes vão para UM nó (o ingress), que então replica para as demais réplicas de cada chunk e confirma ao coordenador:

```text
CLI Client            Coordenador          Ingress (nó)        Réplica A (nó)     Réplica B (nó)
    |                      |                    |                   |                  |
    |-- 1. RequestUpload ->|                    |                   |                  |
    |   (path, size)       |                    |                   |                  |
    |                      |- 2. Placement R-R  |                   |                  |
    |                      |   escolhe ingress  |                   |                  |
    |<- 3. (upload_id, ----|                    |                   |                  |
    |   ingress, chunks)   |                    |                   |                  |
    |                      |                    |                   |                  |
    |-- 4. SetUploadPlan ----------------------> | (guarda plano no PlanStore)         |
    |<- Ack ------------------------------------ |                   |                  |
    |                      |                    |                   |                  |
    |== 5. UploadFile (stream de bytes, 64KB) => | (reagrupa em chunks de 4MB)         |
    |                      |                    |-- 6. StoreChunk -> |                  |
    |                      |                    |-- 6. StoreChunk ----------------------> |
    |                      |                    |   (fan-out paralelo, grava local)    |
    |                      |                    |<- ok ------------- |                  |
    |                      |                    |<- ok -------------------------------- |
    |                      |                    |== 7. QUORUM W=2: 2+ replicas OK =======|
    |                      |<- 8. ConfirmUpload -|                   |                  |
    |                      |   (grava metadados) |                   |                  |
    |<= 9. UploadResult (ok) ===================== |                  |                  |
```

### 3.3 Sequência da Operação GET

O egress monta o arquivo com os chunks locais e busca os faltantes em peers, com failover sequencial entre as réplicas de cada chunk:

```text
CLI Client            Coordenador          Egress (nó)         Peer (nó)
    |                      |                    |                   |
    |-- 1. RequestDownload>|                    |                   |
    |   (logical_path)     |                    |                   |
    |                      |- 2. Le metadados   |                   |
    |                      |   escolhe egress   |                   |
    |                      |   (por localidade) |                   |
    |<- 3. (download_id, --|                    |                   |
    |   egress, total,     |                    |                   |
    |   chunks)            |                    |                   |
    |                      |                    |                   |
    |-- 4. SetDownloadPlan --------------------> | (guarda plano no PlanStore)
    |<- Ack ------------------------------------ |                   |
    |                      |                    |                   |
    |== 5. DownloadFile (download_id) =========> |                   |
    |                      |        (para cada chunk em ordem:)     |
    |                      |          - tem local? le do disco      |
    |                      |          - nao tem? FetchChunk ------->  |
    |                      |                    |<- bytes ---------- |
    |                      |          (se peer falha, tenta o proximo da lista)
    |<= 6. DownloadChunk (stream em ordem) ===== |                   |
    |   (concatena e grava em disco)            |                   |
    |== 7. round-trip byte a byte identico ao arquivo original =====|
```

---

## ⚙️ 4. Especificação de Funcionalidades e Comportamento dos Fluxos

### 4.1 Operação PUT (Injeção Distribuída com Gateway, Handoff, Quórum e Replicação)

1. **Interceptação e Inicialização:** o usuário invoca `python run_cli.py put <caminho_local> <caminho_logico>`. A CLI valida a existência do arquivo local, lê seu conteúdo e calcula o tamanho total em bytes.
2. **Autorização e Roteamento (RequestUpload):** a CLI chama `RequestUpload` no coordenador, informando o caminho lógico e o tamanho. O coordenador calcula `total_chunks = ceil(tamanho / CHUNK_SIZE)`, escolhe o **ingress** entre os nós vivos por round-robin entre arquivos, gera um `upload_id` (UUID) e pré-computa o `ChunkPlacement` de cada chunk com a **membership canônica** (round-robin determinístico). Registra o upload como pendente e devolve à CLI o `upload_id`, o endereço do ingress e o mapa completo de chunks.
3. **Handoff do Plano (SetUploadPlan):** a CLI repassa ao ingress o `upload_id`, o tamanho total e o mapa de `ChunkPlacement`, que o ingress guarda em memória (`PlanStore`). Sem esse passo, o ingress aborta o stream com `FAILED_PRECONDITION` — o gateway precisa saber em quais réplicas gravar cada chunk antes de receber os bytes.
4. **Streaming ao Ingress (UploadFile):** a CLI abre o stream `UploadFile` direto com o ingress, enviando os bytes em pedaços de `STREAM_SIZE` (64 KB), pequenos de propósito para manter baixo o uso de memória e ficar muito abaixo do limite default de mensagem do gRPC.
5. **Reagrupamento e Fan-out no Ingress:** conforme os bytes chegam, o ingress os reacumula em chunks oficiais de `CHUNK_SIZE`. Para cada chunk fechado, grava localmente caso seja uma das réplicas e dispara, **em paralelo (uma thread e um canal por réplica de destino)**, a RPC `StoreChunk` para as demais réplicas do plano.
6. **Consolidação Física nos Workers:** o Worker de destino grava o bloco em `data/nodes/nodeX/chunks/<chunk_id>`. O `chunk_id` segue o padrão estável `<upload_id>_chunk_<índice>`, livre de colisão (UUID por upload).
7. **Quórum de Escrita ($W=2$) e Confirmação:** o ingress só considera um chunk gravado se ao menos **duas réplicas** (contando ele próprio, se for réplica) confirmarem. Concluído o upload, ele chama `ConfirmUpload` no coordenador reportando os chunks gravados; o coordenador então persiste os metadados — é **só aqui** que o arquivo passa a existir para o sistema (aparece no LIST, é encontrável no GET). O stream encerra com `UploadResult(ok=true)`.

### 4.2 Operação GET (Recuperação Descentralizada com Egress por Localidade e Failover)

1. **Autorização (RequestDownload):** o usuário executa `python run_cli.py get <caminho_logico> <destino_local>`. A CLI chama `RequestDownload`; o coordenador lê o `metadata_index.json` (**não recalcula placement** — só lê o que foi persistido no upload), captura o tamanho total e o mapa de chunks, e escolhe o **egress por localidade**: o nó vivo que guarda o maior número de chunks do arquivo, minimizando buscas em peers. Devolve `download_id`, endereço do egress, tamanho total e o mapa de chunks.
2. **Handoff do Plano (SetDownloadPlan):** a CLI repassa ao egress o `download_id`, o tamanho total e o mapa de chunks, guardados no `PlanStore`. Sem isso, o egress aborta com `FAILED_PRECONDITION`.
3. **Streaming do Egress (DownloadFile) com Failover Sequencial:** a CLI abre o stream `DownloadFile`. Para cada chunk, em ordem de índice, o egress verifica se o tem localmente: se sim, lê do disco; se não, busca em um peer via `FetchChunk`, percorrendo as réplicas daquele chunk **sequencialmente** (se um peer não tem o chunk ou não responde, tenta o próximo). A leitura permanece disponível enquanto **pelo menos uma** réplica de cada chunk estiver viva. Os bytes são emitidos em pedaços de `STREAM_SIZE`, em ordem estrita.
4. **Remontagem e Escrita em Disco:** a CLI concatena os fluxos recebidos e grava o arquivo no disco local, replicando perfeitamente o original (round-trip byte a byte verificado).

### 4.3 Operação RM (Remoção Comandada pelo Coordenador)

A deleção é conduzida **pelo coordenador** (não pela CLI), pois é ele a autoridade sobre "este arquivo existe" e o detentor do mapa de chunks.

1. **Invocação e Leitura dos Metadados:** o usuário aciona `python run_cli.py rm <caminho_logico>`. A CLI emite `DeleteFile`; o coordenador lê dos metadados o mapa de chunks do arquivo.
2. **Inversão do Mapa e Purga Paralela:** o coordenador inverte o mapa de "chunk → réplicas" para "nó → seus chunks" e dispara a deleção em paralelo (**uma thread por nó, um canal por nó**), chamando `DeleteChunk` em lote em cada Worker que guarda chunks do arquivo.
3. **Limpeza Física nos Nós:** cada Worker apaga de seu disco os chunks indicados. A deleção é *best-effort*: se um nó está morto, seus chunks contam como falha e o arquivo some dos metadados mesmo assim.
4. **Remoção dos Metadados (ordem: chunks primeiro, metadados depois):** o coordenador remove a entrada do índice **depois** de comandar a deleção física. A ordem é deliberada — se os metadados fossem apagados primeiro e o processo morresse no meio, os chunks ficariam órfãos sem registro (lixo invisível); no sentido inverso, o pior caso é um metadado apontando para chunk já apagado, o que o GET detecta. Prefere-se o erro detectável ao silencioso. O `Local Storage` ainda remove recursivamente subpastas vazias.

### 4.4 Operação LIST (Auditoria do Índice de Metadados)

1. **Invocação:** o usuário digita `python run_cli.py list`. A CLI aciona o stub `ListFiles` no coordenador.
2. **Leitura e Consolidação:** o coordenador lê o `metadata_index.json` e, para cada arquivo, monta uma `FileEntry` com o caminho lógico, o tamanho total em bytes, a quantidade de chunks e o conjunto de nós que possuem pelo menos um chunk daquele arquivo.
3. **Formatação de Saída:** o coordenador devolve um `ListFilesResponse`; a CLI renderiza, por arquivo, uma linha no formato `[FILE] <caminho>  (<n> chunk(s), <bytes> bytes, nodes=<...>)`.

---

## 🧩 5. Aprofundamento dos Conceitos Distribuídos e Engenharia de Rede

### 5.1 Topologia do Cluster: $N=5$ Nós e Fator de Replicação $R=3$

O sistema opera com **$N=5$ nós** e **fator de replicação $R=3$**. O $R=3$ garante que a perda de até duas réplicas de um chunk não cause perda de dados. A escolha de $N=5$ não é arbitrária: é o **menor número de nós que dá graus de liberdade combinatórios reais** ao round-robin. Com $N=3$ e $R=3$, `min(R,N)=3` colocaria todo chunk em todos os nós — não haveria o que balancear, justamente o foco do Marco 3. Com $N=5$ e $R=3$, cada chunk ocupa três dos cinco nós, e a janela de réplicas desliza ao longo do arquivo, distribuindo a carga de forma uniforme.

### 5.2 Quórum de Escrita ($W=2$) e Leitura por Failover

No fan-out do PUT, o ingress só considera um chunk gravado com sucesso se ao menos **$W=2$** réplicas confirmarem. Com $R=3$, exigir duas confirmações garante que a escrita sobreviva à falha de uma réplica, sem exigir que todas as três estejam no ar (o que tornaria o sistema frágil a qualquer falha). Caso o quórum não seja atingido, a operação **falha de forma segura**, em vez de gravar um chunk com replicação insuficiente — preferência por consistência sobre disponibilidade de escrita.

Na leitura, o egress monta o arquivo a partir das réplicas com **failover sequencial**: se um peer não tem ou não responde um chunk, tenta o próximo da lista de réplicas daquele chunk. Isso garante a disponibilidade da leitura enquanto pelo menos uma réplica de cada chunk estiver viva.

### 5.3 Placement Determinístico por Round-Robin

A localização das réplicas de cada chunk é dada por uma regra **pura, determinística e sem estado**, por round-robin sobre o índice do chunk:

$$\text{réplicas}(chunk_i) = [\, N[(i+0) \bmod N],\ N[(i+1) \bmod N],\ \dots,\ N[(i+R-1) \bmod N]\,]$$

A primeira réplica é o *primary*. Com $N=5$ e $R=3$: o chunk 0 vai para $[node1, node2, node3]$, o chunk 1 para $[node2, node3, node4]$, e assim por diante, com a janela deslizando circularmente. Vantagens: **determinismo** (qualquer componente calcula a localização de um bloco só a partir do índice, sem consultar tabelas) e **uniformidade exata** do espalhamento, produzindo uma sequência previsível, fácil de auditar.

**Invariante crítica:** a regra recebe SEMPRE a **membership canônica** (os 5 nós, na ordem fixa), nunca a lista de nós vivos. Se um nó cair e a lista virar 4, o `% N` mudaria e todos os chunks já gravados deixariam de ser encontrados. *Liveness* afeta de qual réplica se *lê* / para onde se re-replica, nunca a fórmula. A função de placement recebe `cluster_size` e falha alto se a lista divergir, blindando contra esse erro.

**Placement decidido no write e persistido (elasticidade):** o placement é calculado **uma vez** no `RequestUpload` e gravado como `ChunkPlacement.replicas` nos metadados; nenhuma operação posterior (GET, DELETE) recalcula posicionamento. Por isso, quando um nó novo entra na membership, é uma operação O(1) no coordenador, com **zero movimentação de dados existentes** — uploads futuros já o incluem, uploads antigos permanecem onde estão.

### 5.4 As Duas Granularidades de Tamanho (`CHUNK_SIZE` × `STREAM_SIZE`)

O gRPC opera sobre HTTP/2 (multiplexação de streams, compressão de cabeçalhos, keep-alive a nível de aplicação) e, por padrão, limita mensagens individuais a 4 MB. A arquitetura resolve isso de forma elegante **sem mexer nesse limite**, separando **duas granularidades** que nunca devem ser confundidas:

- **`CHUNK_SIZE = 4 MB` (a chunknização):** unidade de *placement e replicação*. Define em quantos chunks o arquivo é cortado e quantas entradas de metadado e rodadas de replicação ele gera. Chunks grandes reduzem o overhead de metadados (um arquivo de 256 MB gera 64 chunks de 4 MB, em vez de 4096 de 64 KB — redução de 64×).
- **`STREAM_SIZE = 64 KB` (o pedaço de transporte):** quanto a CLI envia por mensagem gRPC ao subir/baixar. Pequeno de propósito: mantém o uso de memória baixo e fica **muito abaixo** do teto de 4 MB do gRPC.

**Consequência arquitetural:** como todo o tráfego pesado é fatiado em pedaços de 64 KB, nenhuma mensagem individual jamais se aproxima do teto de 4 MB — o ingress reagrupa em chunks de 4 MB em memória, a partir do stream de mensagens de 64 KB. Assim, **não é necessário (nem usado) aumentar o limite de mensagem do gRPC**; o problema clássico de `RESOURCE_EXHAUSTED` por mensagem grande simplesmente não acontece.

---

## 🗂️ 6. Mapeamento e Estrutura do Projeto

Árvore de diretórios do ecossistema na branch `main` (estado integrado). A organização é por papel técnico; os artefatos legados do Marco 2 (sharding por hash, serviço de nó monolítico, cliente interno do coordenador) foram **aposentados** na integração e substituídos pelos módulos abaixo:

```text
Marco3/
├── .venv/                               # Ambiente virtual isolado Python 3.
├── DFS_M3/                              # Diretório mestre do pacote DFS.
│   ├── pyproject.toml                   # Metadados, build-system e empacotamento.
│   ├── requirements.txt                 # Dependências (protobuf, grpcio-tools).
│   ├── ARQUITETURA.md                   # Documento de arquitetura "como implementado".
│   │
│   ├── data/                            # Persistência simulada em disco.
│   │   ├── metadata/                    # Estado do Control Plane (coordenador).
│   │   │   └── metadata_index.json      # Índice mestre dos metadados do DFS.
│   │   └── nodes/                       # Discos isolados dos Workers.
│   │       ├── node1/chunks/            # Chunks do nó 1 (chunks/<chunk_id>).
│   │       ├── node2/chunks/            # Chunks do nó 2.
│   │       ├── node3/chunks/            # Chunks do nó 3.
│   │       ├── node4/chunks/            # Chunks do nó 4.
│   │       └── node5/chunks/            # Chunks do nó 5.
│   │
│   ├── dfs/                             # Pacote Python principal.
│   │   ├── __init__.py                  # Marca a pasta como pacote importável.
│   │   ├── __main__.py                  # Entry point: viabiliza `python -m dfs <cmd>`.
│   │   ├── config.py                    # Constantes (portas, N=5, R=3, CHUNK_SIZE, STREAM_SIZE, heartbeat).
│   │   ├── client.py                    # DataClient (cliente do nó-gateway) + reexporta ControlClient.
│   │   │
│   │   ├── application/                 # Lógica de negócio (sem detalhe de rede).
│   │   │   ├── metadata_service.py      # Índice de arquivos em JSON (transacional, sob lock).
│   │   │   ├── data_service.py          # DataServicer: nó como ingress (PUT) e egress (GET).
│   │   │   └── replication_service.py   # ReplicationServicer: CRUD de chunks no disco.
│   │   │
│   │   ├── cluster/                     # Infraestrutura de cluster compartilhada.
│   │   │   ├── node_registry.py         # Membership canônica + estado vivo (heartbeat).
│   │   │   ├── placement.py             # Round-robin determinístico (fonte de verdade do placement).
│   │   │   ├── plan_store.py            # PlanStore + DataPlaneServicer (handoff do plano CLI -> nó).
│   │   │   ├── control_client.py        # Cliente do ControlService (usado por nó e CLI).
│   │   │   └── replication_client.py    # Cliente do ReplicationService (coordenador: deleção; nós: fan-out/fetch).
│   │   │
│   │   ├── interface/                   # Adaptadores gRPC + pontos de entrada de processo.
│   │   │   ├── cli.py                   # CLI: parsing, menu interativo e cliente persistente.
│   │   │   ├── server.py                # COORDENADOR: ControlServiceServicer (porta 9100).
│   │   │   └── storage_node.py          # NÓ: hospeda Data + Replication + DataPlane; heartbeat.
│   │   │
│   │   ├── storage/                     # Persistência física de baixo nível.
│   │   │   └── local_storage.py         # I/O de bytes; API por caminho lógico e por chunk_id.
│   │   │
│   │   └── pb/                          # Contratos e stubs gerados.
│   │       ├── __init__.py
│   │       ├── dfs.proto                # Contrato compartilhado: os três serviços (fonte de verdade).
│   │       ├── dataplane.proto          # Contrato interno do plano de dados (handoff do plano).
│   │       ├── dfs_pb2.py / dfs_pb2_grpc.py             # Gerados de dfs.proto. NÃO editar à mão.
│   │       └── dataplane_pb2.py / dataplane_pb2_grpc.py # Gerados de dataplane.proto. NÃO editar à mão.
│   │
│   └── scripts/
│       └── start_coordinator.py         # Atalho para subir apenas o coordenador.
│
├── README.md                            # Este manual.
├── run_cluster.py                       # Orquestrador: sobe os 5 nós + o coordenador.
└── run_cli.py                           # Ponto de entrada da CLI (Data Plane).
```

---

## 🧭 7. O Que Faz Cada Arquivo

### 7.1 Raiz `Marco3/`

- **`run_cluster.py`:** maestro de processos. Via `subprocess`, sobe as **cinco** instâncias de nós (portas 9101 a 9105) e, **em seguida**, o coordenador (porta 9100). Os nós sobem antes de propósito: quando o coordenador começa a atender, os nós já estão prontos. Lê `NODE_ORDER` do `config.py`, então adicionar nós no config faz o runner subi-los automaticamente, sem editar o runner. É normal um aviso inicial de heartbeat falhando até o coordenador subir — o retry resolve no ciclo seguinte.
- **`run_cli.py`:** portal de entrada do usuário. Insere `DFS_M3/` no `sys.path`, importa `dfs.interface.cli` e delega para o seu `main`, permitindo invocar os comandos sem navegar pastas. Sem argumentos, abre o **modo interativo persistente**.

### 7.2 Core `DFS_M3/dfs/`

- **`config.py`:** centraliza endereço/porta do coordenador (`127.0.0.1:9100`), porta base dos nós (`9101`; node1→9101 … node5→9105), número de nós (`NODE_COUNT = 5`), fator de replicação (`REPLICATION_FACTOR = 3`), as duas granularidades (`CHUNK_SIZE = 4 MB`, `STREAM_SIZE = 64 KB`), os limiares de heartbeat (`HEARTBEAT_INTERVAL = 2s`, `HEARTBEAT_SUSPECT = 4s`, `HEARTBEAT_DEAD = 8s`) e os caminhos de dados. A configuração dos nós é gerada por `build_nodes(NODE_COUNT)`.
- **`client.py`:** implementa o `DataClient` (cliente do nó-gateway). Encapsula os stubs do `DataService` e do `DataPlaneService` na mesma conexão e provê `set_upload_plan`/`set_download_plan` (handoff), `upload` (gera o stream de `UploadChunk` em pedaços de `STREAM_SIZE`) e `download` (consome o stream de `DownloadChunk` e concatena). Reexporta o `ControlClient` para a CLI.
- **`__main__.py`:** entry point do pacote (`python -m dfs <cmd>`); importa e chama o `main` da CLI.

### 7.3 Camada `application/`

- **`metadata_service.py`:** motor transacional de persistência. Gerencia o `metadata_index.json` sob lock, com `put_file` (recebe caminho, tamanho total e lista de chunks com réplicas), busca, deleção e listagem. Não conhece tipos do protobuf — quem chama (`ConfirmUpload`) converte os `ChunkPlacement` em dicionários simples antes de gravar.
- **`data_service.py`:** implementa o `DataServicer`. No `UploadFile` (ingress), carrega o plano do `PlanStore`, reagrupa os bytes em chunks de `CHUNK_SIZE`, grava local se for réplica, dispara o fan-out paralelo (`StoreChunk`), valida o quórum $W=2$ e chama `ConfirmUpload`. No `DownloadFile` (egress), carrega o plano, monta o arquivo lendo chunks locais e buscando os faltantes em peers (`FetchChunk`, failover sequencial), emitindo em ordem.
- **`replication_service.py`:** implementa o `ReplicationServicer` (comunicação nó-a-nó e coordenador): `StoreChunk` (recebe e grava um chunk em stream), `FetchChunk` (lê e emite um chunk local em pedaços de `STREAM_SIZE`), `DeleteChunk` (apaga um chunk) e `ListChunks` (inventário local).

### 7.4 Camada `cluster/`

- **`node_registry.py`:** catálogo com duas responsabilidades separadas: a **membership canônica** (estática, do config, consumida pelo placement) e o **estado vivo** (dinâmico, por `register_node`/`record_heartbeat`). Classifica cada nó como ALIVE / SUSPECT / DEAD pelo tempo de silêncio, num cálculo preguiçoso na hora da consulta. Expõe `canonical_members()` (placement) e `alive_members()` (roteamento de ingress/egress).
- **`placement.py`:** função pura de round-robin determinístico. `replicas_for_chunk(chunk_index, nodes, R, cluster_size)` devolve as réplicas pela regra `(i+offset) % N`; `primary_replica` devolve só o primary; `ingress_for_file` escolhe o ingress por round-robin entre arquivos. Recebe `cluster_size` como blindagem: se a lista divergir da canônica, estoura em vez de calcular errado.
- **`plan_store.py`:** implementa o `PlanStore` (mapa em memória, sob lock, de `upload_id`/`download_id` → plano de chunks, com limpeza após a operação) e o `DataPlaneServicer` (`SetUploadPlan`/`SetDownloadPlan`). Materializa o handoff do plano da CLI para o nó.
- **`control_client.py`:** cliente do `ControlService`, usado pelo nó (`register`, `heartbeat`, `confirm_upload`) e pela CLI (`request_upload`, `request_download`, `delete_file`, `list_files`).
- **`replication_client.py`:** reúne os dois clientes do `ReplicationService`: as funções de coordenador (`delete_node_chunks`/`delete_one_chunk`, usadas no `DeleteFile`, reusando um canal por nó) e a classe `ReplicationClient` (usada pelos nós no fan-out via `store_chunk` e no failover via `fetch_chunk`).

### 7.5 Camadas `interface/`, `storage/` e `pb/`

- **`cli.py`:** interface de linha de comando. Faz o parsing dos comandos (`put`/`get`/`list`/`rm`/`menu`), exibe o menu interativo e mantém o loop de sessão. Implementa o fluxo de **três chamadas** e mantém um **cliente persistente** (um único `ControlClient` reusado na sessão interativa; o `DataClient` é aberto por operação, pois o nó-gateway varia por arquivo). Envolve cada comando em tratamento de `grpc.RpcError` para a sessão não morrer se um nó/coordenador estiver fora.
- **`server.py`:** inicializa o servidor gRPC do coordenador. Implementa o `ControlServiceServicer` com as sete RPCs (`RegisterNode`, `Heartbeat`, `RequestUpload`, `ConfirmUpload`, `RequestDownload`, `DeleteFile`, `ListFiles`), usando `NodeRegistry`, `placement.py`, `MetadataService` e `replication_client`. Registra o `ControlServiceServicer` e escuta em `127.0.0.1:9100`.
- **`storage_node.py`:** lançador de um nó. Sobe, na **mesma porta**, os três serviços do nó (`DataService`, `ReplicationService`, `DataPlaneService`) e dispara, em background, o registro e o heartbeat junto ao coordenador (a cada `HEARTBEAT_INTERVAL`, com o block report). Uso: `python -m dfs.interface.storage_node --node-id node1`.
- **`local_storage.py`:** I/O de baixo nível. Mantém a API por caminho lógico (`put`/`get`/`delete`/`list_files`) e a API por `chunk_id` (`store_chunk`/`read_chunk`/`has_chunk`/`delete_chunk`/`list_chunk_ids`), gravando cada chunk em `chunks/<chunk_id>` **sem extensão** (para casar com o regex `_chunk_\d+$` do observer), com isolamento contra *path traversal* e remoção de subpastas vazias.
- **`dfs.proto`:** contrato IDL definitivo (pacote `dfs.v1`), fonte de verdade dos três serviços principais (`ControlService`, `DataService`, `ReplicationService`) e de todas as mensagens.
- **`dataplane.proto`:** contrato interno do plano de dados (pacote `dfs.dataplane`). Declara o `DataPlaneService` (`SetUploadPlan`/`SetDownloadPlan`) e reusa `ChunkPlacement` e `Ack` do `dfs.proto` via import, sem redefinir nada. Separado de propósito para manter o contrato compartilhado e os stubs do coordenador intocados.
- **`dfs_pb2.py` / `dfs_pb2_grpc.py` / `dataplane_pb2.py` / `dataplane_pb2_grpc.py`:** código gerado pelo `grpc_tools.protoc` a partir dos `.proto`. Contêm as mensagens e os stubs/servicers. **Não devem ser editados manualmente** — sempre regenerar (ver seção 8).

---

## 🧱 8. Geração dos Stubs gRPC

Compilar SEMPRE a partir da raiz do pacote (`DFS_M3/`), com `-I=.`, para os **dois** `.proto`:

```bash
cd DFS_M3
python -m grpc_tools.protoc -I=. --python_out=. --grpc_python_out=. dfs/pb/dfs.proto
python -m grpc_tools.protoc -I=. --python_out=. --grpc_python_out=. dfs/pb/dataplane.proto
```

O `-I=.` faz o `protoc` enxergar o caminho como `dfs/pb/dfs.proto` e gerar o import qualificado `from dfs.pb import dfs_pb2` (e o `dataplane_pb2` reusa `dfs_pb2` por esse mesmo import). Com `-I=dfs/pb` ele geraria `import dfs_pb2` (plano), que quebra em runtime com `ModuleNotFoundError`. Nunca editar os arquivos `*_pb2*.py` à mão.

---

## 🚀 9. Guia de Execução

A partir do diretório `Marco3/`:

### Step 1 — Provisionar o ambiente virtual
```bash
python -m venv .venv
```

### Step 2 — Ativar o ambiente virtual
- **Linux / macOS (Bash/Zsh):** `source .venv/bin/activate`
- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`
- **Windows (CMD):** `.venv\Scripts\activate.bat`
- **Windows + Git Bash (VS Code):** `source .venv/Scripts/activate`

### Step 3 — Instalar as dependências
```bash
pip install -r DFS_M3/requirements.txt
```

### Step 4 — Compilar os contratos (ver seção 8)
```bash
cd DFS_M3
python -m grpc_tools.protoc -I=. --python_out=. --grpc_python_out=. dfs/pb/dfs.proto
python -m grpc_tools.protoc -I=. --python_out=. --grpc_python_out=. dfs/pb/dataplane.proto
cd ..
```

### Step 5 — Subir o cluster completo
```bash
python run_cluster.py
```
*Este terminal exibe os logs unificados do coordenador e dos 5 nós. Mantenha-o aberto durante o uso. É normal ver um aviso inicial de heartbeat falhando até o coordenador subir (os nós sobem antes); o retry resolve no ciclo seguinte.*

### Step 6 — Interagir via CLI (em outro terminal, com a venv ativada)
```bash
python run_cli.py <comando> [argumentos]
```

> **Higiene de dados:** ao mudar `NODE_COUNT` ou ao migrar de uma versão antiga dos metadados, apague `DFS_M3/data/metadata/*` e `DFS_M3/data/nodes/*` antes de subir o cluster — dados gravados sob um placement diferente ficam inacessíveis e poluem o `list`.

---

## 🧪 10. Exemplos de Uso

### 10.1 Preparar um arquivo local
```bash
echo "Sistemas distribuidos com gateway e round-robin via gRPC - Marco 3" > DFS_M3/teste.txt
```

### 10.2 Enviar um arquivo (PUT)
```bash
python run_cli.py put DFS_M3/teste.txt documentos/financeiro/dados.txt
```

### 10.3 Listar o índice (LIST)
```bash
python run_cli.py list
```

### 10.4 Baixar um arquivo (GET)
```bash
python run_cli.py get documentos/financeiro/dados.txt copia_recuperada.txt
```

### 10.5 Remover um arquivo (RM)
```bash
python run_cli.py rm documentos/financeiro/dados.txt
```

### 10.6 Modo interativo persistente
```bash
python run_cli.py
```
*Mantém o canal gRPC com o coordenador aberto na sessão, eliminando o overhead de reabrir conexão e refazer o handshake a cada comando. No prompt `dfs>`, ficam disponíveis `put`, `get`, `list`, `rm` e `menu`/`help`.*

---

## 🔍 11. Traces Lógicos das Operações

### 11.1 PUT (escrita distribuída via gateway)

```text
[Operador CLI] ---> put teste.txt docs/documento.txt
  |
  +---> CLI le o arquivo local (ex: 8MB) e calcula o tamanho total
  |
  +---> CLI emite [RequestUpload(path, size)] ---> [Coordenador (9100)]
          +---> total_chunks = ceil(size / 4MB) -> 2 chunks
          +---> escolhe o INGRESS entre os nos vivos (round-robin entre arquivos)
          +---> pre-computa o placement round-robin com a membership canonica:
          |       - chunk_0 -> [node1, node2, node3]
          |       - chunk_1 -> [node2, node3, node4]
          +---> gera upload_id (UUID), registra upload pendente
  |
[Coordenador] ---> [upload_id, ingress, mapa de ChunkPlacement] ---> [CLI]
  |
  +---> CLI emite [SetUploadPlan(upload_id, total, chunks)] ---> [Ingress]
  |       (ingress guarda o plano no PlanStore; sem isso, abortaria com FAILED_PRECONDITION)
  |
  +===> CLI abre [UploadFile] -> Ingress, bytes em pedacos de 64KB (STREAM_SIZE)
  |       +---> Ingress reagrupa em chunks de 4MB (CHUNK_SIZE)
  |       +===> CHUNK 0 (replicas node1,node2,node3): grava local + fan-out paralelo [StoreChunk]
  |       |       node1 OK, node2 OK, node3 TIMEOUT -> QUORUM W=2 atingido
  |       +===> CHUNK 1 (replicas node2,node3,node4): fan-out [StoreChunk] -> todas OK
  |
  +---> Ingress emite [ConfirmUpload(upload_id, chunks, total)] ---> [Coordenador]
  |       (coordenador converte ChunkPlacement -> dict e grava no metadata_index.json)
  |       (SO AGORA o arquivo existe para o sistema: aparece no LIST, encontravel no GET)
  |
[Ingress] ---> [UploadResult(ok=true)] ---> [CLI]   (CLI imprime "upload concluído")
```

### 11.2 GET (leitura com egress por localidade e failover sequencial)

```text
[Operador CLI] ---> get docs/documento.txt copia.txt
  |
  +---> CLI emite [RequestDownload(logical_path)] ---> [Coordenador (9100)]
          +---> busca no metadata_index.json (NAO recalcula placement)
          +---> le o tamanho total e o mapa de chunks/replicas persistido
          +---> escolhe o EGRESS por LOCALIDADE: no vivo com mais chunks do arquivo
          +---> gera download_id (UUID)
  |
[Coordenador] ---> [download_id, egress, total, mapa de chunks] ---> [CLI]
  |
  +---> CLI emite [SetDownloadPlan(download_id, total, chunks)] ---> [Egress]
  |
  +===> CLI abre [DownloadFile(download_id)] -> Egress
  |       +---> para cada chunk, em ordem:
  |               - tem local? le do disco
  |               - nao tem? FetchChunk em peer (failover sequencial pela lista de replicas)
  |                   * peer sem o chunk / sem resposta -> tenta o proximo da lista
  |                   * disponivel enquanto AO MENOS UMA replica do chunk estiver viva
  |               - emite os bytes em pedacos de 64KB (STREAM_SIZE), em ordem
  |
[Egress] ===> stream de DownloadChunk ===> [CLI]  (concatena e grava em copia.txt)
  |
[CLI] ---> "Arquivo baixado (N bytes)" — round-trip byte a byte identico ao original.
```

### 11.3 RM (exclusão comandada pelo coordenador)

```text
[Operador CLI] ---> rm docs/documento.txt
  |
  +---> CLI emite [DeleteFile(logical_path)] ---> [Coordenador (9100)]
          +---> le os metadados (mapa chunk -> replicas)
          +---> inverte para "no -> seus chunks"
          +---> dispara delecao em PARALELO (uma thread/canal por no):
          |       [DeleteChunk em lote] -> node1, node2, node4 (apagam seus chunks)
          |       node3 MORTO -> chunks contam como falha (best-effort)
          +---> remove a entrada do metadata_index.json (chunks primeiro, metadados depois)
  |
[Coordenador] ---> [Ack(ok, "N replicas apagadas, M falhas (best-effort)")] ---> [CLI]
```

---

## 🛠️ 12. Decisões de Projeto e Alinhamento com o Teorema CAP

O design do DFS no Marco 3 foi norteado pelo **Teorema CAP (Consistency, Availability, Partition Tolerance)** de Eric Brewer.

- **Prioridade pela Consistência da escrita (quadrante CP):** diante de uma falha que inviabilize o **quórum de escrita $W=2$** de um chunk, o sistema prefere **falhar a operação de forma segura** a gravar um chunk com replicação insuficiente, evitando estados inconsistentes. A leitura prioriza disponibilidade dentro do que os dados persistidos permitem, via failover sequencial entre réplicas — enquanto ao menos uma réplica de cada chunk estiver viva, o GET é servido.
- **Remoção de gargalos por descentralização de I/O:** o modelo gateway tira o coordenador do caminho dos bytes, que fluem da CLI para um nó (ingress/egress) e entre nós, diretamente. A vazão agregada do cluster cresce com a adição de nós — mesmo princípio do NameNode/DataNodes do HDFS.
- **Elasticidade sem movimentação de dados:** como o placement é decidido uma única vez no upload e persistido, a entrada de um nó novo é O(1) no coordenador, com **zero movimentação de dados existentes**. Uploads futuros já o incluem; uploads antigos permanecem onde estão.
- **Localidade de dados na leitura:** o egress é o nó vivo que já guarda o maior número de chunks do arquivo, minimizando as buscas em peers (`FetchChunk`) e o tráfego inter-nós.
- **Identidade vs. estado vivo no registro:** posicionamento usa a membership canônica (inclui nós temporariamente fora, pois um nó em manutenção ainda é destino válido de placement); roteamento pontual (ingress/egress) usa só os vivos. Misturar as duas listas quebraria o determinismo do placement.

---

## ✅ 13. Critérios Técnicos Atendidos no Marco 3

- Rede distribuída nativa sobre canais multiplexados gRPC e transporte HTTP/2.
- Contratos de interface tipados em Protocol Buffers (IDL), em **dois** arquivos: `dfs.proto` (compartilhado, três serviços) e `dataplane.proto` (interno, handoff do plano).
- Isolamento arquitetural absoluto entre Control Plane e Data Plane (**modelo gateway**: o coordenador nunca toca em bytes).
- **Replicação ativa** síncrona/concorrente (fan-out paralelo) com fator $R=3$ sobre $N=5$ nós.
- **Quórum de escrita $W=2$** no fan-out do PUT.
- **Leitura por failover sequencial** entre réplicas (disponível enquanto ao menos uma réplica de cada chunk estiver viva).
- **Placement round-robin determinístico** por índice de chunk, calculado uma vez no write e persistido (elasticidade sem movimentação de dados).
- **Supervisão de nós por heartbeat** (ALIVE / SUSPECT / DEAD, limiares 2/4/8 s) com block report, usada no roteamento de ingress/egress.
- **Handoff do plano de chunks** (`SetUploadPlan`/`SetDownloadPlan`) via contrato interno, sem tocar no contrato compartilhado.
- **Deleção comandada pelo coordenador**, em paralelo (uma thread por nó), best-effort, com ordem segura (chunks antes, metadados depois).
- **Cliente interativo persistente**, com cache do canal gRPC do coordenador.
- Limpeza física de storage local com remoção recursiva de diretórios vazios.
- **Integração ponta a ponta validada:** `put`/`get`/`list`/`rm` com verificação byte a byte do round-trip, contra coordenador real e cinco nós reais.

---

## 👨‍💻 14. Observações Finais e Autores

- O ecossistema opera sob **placement round-robin determinístico por índice de chunk**, dispensando bancos de dados para mapeamento físico de blocos; a localização de qualquer chunk é calculável a partir do seu índice e da membership canônica.
- O placement EXIGE a membership canônica (os $N=5$ nós, na ordem fixa) — passar a lista de nós vivos no lugar dela deslocaria o `% N` e tornaria chunks já gravados inacessíveis; a função se blinda contra isso exigindo `cluster_size`.
- As duas granularidades (`CHUNK_SIZE = 4 MB` para placement/replicação e `STREAM_SIZE = 64 KB` para transporte) **nunca devem ser confundidas**; é essa separação que dispensa inflar o limite de mensagem do gRPC.
- O caminho local de origem deve existir com permissão de leitura antes do `put` (a CLI resolve caminhos relativos a partir de onde é executada).
- O caminho virtual de um arquivo no DFS é independente do nome/local do arquivo físico na máquina do usuário, operando como uma camada de abstração.
- Mudanças no `dfs.proto`, no `placement.py` ou na arquitetura exigem combinação prévia entre a dupla e entram na `main` via PR.

### Autores

- **Higor Ferreira Silva** — Matrícula: 202201635 — Plano de Dados (DataService, ReplicationService, DataPlaneService, PlanStore, LocalStorage, CLI de dados).
- **Vitória Mendonça** — Matrícula: 202004699 — Plano de Controle (ControlService/coordenador, NodeRegistry, placement, metadados, deleção comandada).
