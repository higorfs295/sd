# Distributed File System (DFS) — Marco 3
## Manual de Arquitetura, Engenharia de Sistemas Distribuídos e Documentação Técnica Operacional

---

## 📌 1. Visão Geral Expandida

Este projeto acadêmico e de engenharia de software consiste na implementação de uma infraestrutura robusta de **Sistema de Arquivos Distribuído (DFS - Distributed File System)** desenvolvida integralmente na linguagem Python. Historicamente, o sistema evoluiu a partir de uma arquitetura legada (Marcos 1 e 2) baseada em sockets TCP brutos (puros), os quais dependiam de um mecanismo de controle de fluxo manual e arbitrário denominado *framing por tamanho* (implementado via `frame.py` e `protocol.py`). No **Marco 3**, o ecossistema sofre uma completa disrupção arquitetural, abandonando as amarras do gerenciamento manual de buffers de rede de baixo nível e migrando de forma definitiva para uma infraestrutura de rede moderna, nativa, assíncrona e multiplexada baseada em **gRPC** (Remote Procedure Calls) rodando sobre a camada de transporte **HTTP/2**, utilizando o **Protocol Buffers (Protobuf)** tanto como linguagem de definição de interface (IDL - Interface Definition Language) estrita quanto como motor de serialização binária ultraotimizada em tempo de execução (runtime).

No contexto do **Marco 3**, a topologia lógica e física do cluster deixa de operar sob a ótica de um Sharding simples e estático, despido de qualquer tolerância a falhas, e transmuta-se em um ecossistema distribuído de alta complexidade, descentralizado, coordenado e tolerante a falhas de nós. A decisão arquitetural mais importante deste marco — e que organiza todo o resto do sistema — é a **separação rigorosa entre o plano de controle (control plane) e o plano de dados (data plane)**, na qual o coordenador centraliza unicamente as decisões e os metadados, enquanto os bytes pesados dos arquivos trafegam diretamente entre o cliente e os nós, sem nunca passar pelo coordenador. O sistema passa a ser estruturado com base nos seguintes pilares fundamentais:

- **Múltiplos Nós de Armazenamento Coordenados e Independentes (Workers):** Instâncias autônomas (cinco no total, $N=5$) que gerenciam seus próprios discos rígidos virtuais, respondendo a chamadas de I/O de rede e cooperando entre si para replicar blocos, sem que o coordenador intermedeie o tráfego de dados.
- **Particionamento e Fragmentação de Arquivos em Chunks Líquidos:** Arquivos volumosos inseridos no ecossistema não são armazenados como blocos monolíticos. O sistema realiza o fatiamento lógico do arquivo em pedaços binários de tamanho fixo configurável (`CHUNK_SIZE = 4 MB`), otimizando o paralelismo e a distribuição espacial da carga de disco. Esse corte é responsabilidade de um **nó-gateway (ingress)**, e não do cliente nem do coordenador.
- **Replicação Ativa de Dados com Fator Fixo ($R=3$):** Para cada bloco lógico (*chunk*) gerado pelo processo de fragmentação, o ecossistema propaga de forma concorrente três réplicas idênticas em servidores físicos distintos, mitigando riscos de perda de dados decorrentes de falhas de hardware. A propagação é um *fan-out* paralelo disparado pelo nó-gateway, não pelo coordenador.
- **Placement Determinístico por Round-Robin (não mais Hashing):** A localização das réplicas de cada chunk é decidida por uma regra pura e determinística de round-robin sobre o índice do chunk (módulo o número de nós), substituindo definitivamente o sharding por hash do Marco 2. A regra é calculada **uma única vez** no momento do upload e persistida nos metadados, jamais recalculada.
- **Quórum de Escrita Estável ($W=2$):** O nó-gateway só considera um chunk gravado com sucesso quando ao menos duas das três réplicas confirmam o armazenamento, garantindo que a escrita sobreviva à falha de uma réplica durante o upload. A leitura, por sua vez, opera com **failover sequencial** entre réplicas (descrito na seção 4.2), garantindo disponibilidade enquanto ao menos uma réplica de cada chunk estiver viva.
- **Supervisão de Nós por Heartbeat (ALIVE / SUSPECT / DEAD):** Cada nó registra-se ao subir e envia batimentos periódicos com inventário de chunks (*block report*); o coordenador classifica cada nó pelo tempo de silêncio e o usa para roteamento (escolha de ingress/egress).
- **Handoff de Plano via Contrato Interno (`dataplane.proto`):** O cliente repassa ao nó-gateway o mapa de chunks recebido do coordenador **antes** de abrir o stream de bytes, por meio de um serviço interno do plano de dados, mantendo o contrato compartilhado (`dfs.proto`) intocado.
- **Controle de Versionamento Global e Atômico `[Planejado — Marco 4]`:** O carimbo de versão por chunk e o quórum de leitura $R=2$ com anti-entropia são descritos como objetivo de design para consistência forte linearizável, ainda não implementados no Marco 3 (cuja leitura usa failover sequencial).
- **Motor de Computação Distribuída Orientado a Localidade de Dados (MapReduce) `[Não Feito]`:** Acoplamento de uma engine de processamento paralelo que envia a computação em direção ao local físico onde os blocos de dados residem, minimizando drasticamente o tráfego e o overhead de rede no cluster.
- **Separação Estrita entre Control Plane (Plano de Controle) e Data Plane (Plano de Dados):** Descentralização radical do tráfego de rede do cluster. O nó centralizador (Coordenador) é completamente removido do fluxo de passagem de bytes pesados, atuando única e exclusivamente na resolução lógica de rotas, posicionamento (placement) e metadados.
- **Metadados Persistentes com Rastreabilidade Multinível:** O estado lógico de todo o cluster é mantido de forma transacional e transparente por meio de um índice mestre baseado em JSON, mapeando com precisão as coordenadas físicas, lógicas e de tamanho de cada arquivo, chunk e réplica.

A meta principal deste terceiro marco regulatório do projeto é validar o comportamento macro e micro do DFS sob estresse, concorrência agressiva de escrita/leitura e cenários degradados de falhas de rede ou colapso de servidores. O objetivo é certificar que o sistema consiga replicar dados de maneira íntegra, manter-se online e consistente mesmo com a queda abrupta de nós de armazenamento, e fornecer um caminho de dados que escala com a adição de novos nós. **O Marco 3 foi integrado e validado de ponta a ponta:** as operações `put`, `get`, `list` e `rm` funcionam com verificação byte a byte do round-trip, com o coordenador real (`ControlService`) e os cinco nós reais cooperando via gRPC.

---

## 🧠 2. Arquitetura Geral e Decomposição de Planos

O DFS foi arquitetado seguindo padrões modernos de sistemas distribuídos de larga escala (fortemente inspirado em conceitos do Google File System e Apache HDFS), organizando-se em camadas rigidamente isoladas de responsabilidade. Essa abordagem garante o desacoplamento absoluto entre as interfaces de usuário, a lógica centralizada de orquestração do cluster e os subsistemas de persistência física em disco. A organização do código é feita por **papel técnico** (interface, aplicação, cluster, storage), não por componente, de forma que um servicer gRPC (adaptador de rede) delega para um serviço de lógica, que por sua vez usa a infraestrutura de cluster e a persistência local.

### 2.1 O Plano de Controle (Control Plane) vs. O Plano de Dados (Data Plane)

O principal avanço arquitetural do Marco 3 reside na separação definitiva dos fluxos de sinalização e de tráfego pesado:

- **O Control Plane (Coordenador):** Gerencia exclusivamente metadados. Ele opera como um servidor gRPC ultraleve encarregado de escutar solicitações de mapeamento estrutural. Quando a CLI solicita um `put`, o Coordenador calcula o **placement round-robin determinístico** de cada chunk, escolhe qual nó atuará como ingress, reserva um `upload_id` e devolve uma "receita de bolo" (o mapa de `ChunkPlacement`) contendo os IDs e endereços (host:port) dos nós que devem abrigar cada réplica. O Coordenador **nunca** abre buffers para ler ou transmitir os bytes dos arquivos dos usuários; ele apenas decide e persiste. A persistência dos metadados ocorre somente quando o ingress confirma o upload (`ConfirmUpload`).
- **O Data Plane (CLI e Workers):** Compreende a malha de tráfego de bytes brutos. De posse da tabela de roteamento fornecida pelo Control Plane, a CLI (atuando como um *cliente fraco*, que não fatia nem decide nada) entrega o plano ao nó-gateway via `SetUploadPlan` e em seguida abre um stream gRPC diretamente com esse nó (ingress no PUT, egress no GET). Os dados trafegam ponto a ponto pela periferia da rede, escalando o throughput global de forma linear, pois a adição de novos nós expande a largura de banda de I/O de maneira diretamente proporcional, sem criar gargalos no nó central. A replicação entre réplicas e a busca de chunks em peers também ocorrem diretamente entre nós, jamais pelo coordenador.

### 2.2 Visão Detalhada dos Componentes

Para mapear a topologia do sistema, os seguintes componentes de software cooperam dinamicamente:

- **CLI Client (Interface de Linha de Comando):** Componente que intercepta as entradas do operador humano, executa o parsing de argumentos e gerencia o ciclo de vida de um cliente persistente. Ele mantém um canal HTTP/2 aquecido (*warmed-up gRPC stub*) com o coordenador durante toda a sessão interativa, evitando a latência repetitiva de handshake TCP a cada comando. Faz **três chamadas** no PUT (RequestUpload → SetUploadPlan → UploadFile) e três simétricas no GET.
- **Coordenador Principal (Master Node):** Servidor centralizado (`ControlService`) detentor da inteligência de controle: posicionamento de chunks, escolha de ingress/egress, supervisão de nós via heartbeat e centralização lógica do índice do sistema de arquivos distribuído. Escuta em `127.0.0.1:9100`.
- **Placement Engine (Motor de Posicionamento Round-Robin):** Módulo matemático puro (`placement.py`) responsável por computar, de forma determinística e sem estado, as réplicas de cada chunk a partir do seu índice ordinal (round-robin módulo $N$). Substitui o antigo Sharding Manager por hash. A mesma função decide o ingress de cada arquivo (round-robin entre arquivos).
- **Node Registry (Registro de Nós):** Catálogo mantido em memória pelo Coordenador que separa duas responsabilidades: a **membership canônica** (lista fixa e ordenada dos $N$ nós, lida do `config.py`, consumida pelo placement) e o **estado vivo** (quem está ALIVE / SUSPECT / DEAD agora, atualizado por registro e heartbeat, consumido pelo roteamento).
- **Replication Client Interno:** Cliente gRPC usado em dois papéis: pelo **coordenador**, para comandar a deleção física de chunks nos nós (`DeleteChunk`); e pelos **nós**, para o fan-out de réplicas (ingress → réplicas, `StoreChunk`) e a busca de chunks em peers (egress → peer, `FetchChunk`). Vive em `replication_client.py`.
- **Control Service (Camada de Aplicação do Coordenador):** Implementa concretamente o `ControlServiceServicer` (em `server.py`), aplicando as regras de negócio do plano de controle: autorização de upload/download, confirmação de upload, deleção comandada e listagem. Não toca em bytes.
- **Metadata Service (Serviço de Metadados):** Componente transacional (`metadata_service.py`) encarregado de efetuar operações de leitura e escrita protegidas por lock no arquivo de persistência mestre `metadata_index.json`, provendo isolamento contra corrupção por concorrência. Armazena, por arquivo, o tamanho total, a lista de chunks e, por chunk, as réplicas.
- **Data Service (Camada de Aplicação do Worker — Gateway):** Serviço gRPC (`data_service.py`) hospedado em cada Nó. Implementa o `UploadFile` (o nó como **ingress**: recebe o stream, reagrupa em chunks de `CHUNK_SIZE`, grava local se for réplica e dispara o fan-out paralelo) e o `DownloadFile` (o nó como **egress**: junta chunks locais e busca os faltantes em peers, emitindo o stream em ordem).
- **Replication Service (Camada de Aplicação do Worker — Nó-a-nó):** Serviço gRPC (`replication_service.py`) que atende outros nós e o coordenador: `StoreChunk` (recebe um chunk de uma réplica), `FetchChunk` (serve um chunk a um peer), `DeleteChunk` (apaga um chunk a mando do coordenador) e `ListChunks` (diagnóstico).
- **Data Plane Service (Handoff do Plano):** Serviço gRPC interno (`plan_store.py`, contrato em `dataplane.proto`) exposto pelos nós na mesma porta do DataService. Recebe da CLI o plano de chunks (`SetUploadPlan` / `SetDownloadPlan`) antes do stream e o guarda em memória (`PlanStore`), para o DataService consumir durante a operação.
- **Local Storage Manager (Gerenciador de Armazenamento Local):** Módulo (`local_storage.py`) que encapsula as chamadas de I/O do sistema operacional hospedeiro. Mantém a API legada por caminho lógico e adiciona a API por `chunk_id`, gravando cada chunk em `chunks/<chunk_id>` (sem extensão, para casar com o regex do observer), além de garantir isolamento de caminhos.
- **MapReduce Service `[Não Feito]`:** Componente mestre do plano de controle encarregado de fracionar expressões de busca analítica, mapear a proximidade física dos blocos correspondentes e consolidar (*Reduce*) os vetores numéricos leves devolvidos pela periferia do cluster.
- **Node Compute Service `[Não Feito]`:** Motor de processamento local acoplado ao Worker. Ele varre os arquivos locais persistidos em disco, aplicando filtros algorítmicos em memória (*Map*) sem realizar qualquer tráfego de rede pesado de arquivos.

---

## 🔍 3. Diagramas de Fluxo e Arquitetura

Para documentar visualmente o comportamento operacional e o tráfego de rede estabelecido entre os componentes do DFS no Marco 3, esta seção expõe os diagramas de blocos de comunicação e as sequências de eventos cronológicos.

### 3.1 Topologia de Rede e Fluxo Geral de Comunicação

O diagrama abaixo ilustra a separação absoluta dos planos de comunicação. As linhas pontilhadas simbolizam tráfego puro de controle e sinalização de metadados (CLI ↔ coordenador; nós → coordenador no registro/heartbeat), enquanto as linhas duplas contínuas simbolizam o transporte maciço de payloads binários (bytes de dados, CLI ↔ nó-gateway e nó ↔ nó):

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
                  |              NO-GATEWAY (ingress/egress)       |
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

---

### 3.2 Diagrama de Sequência Comportamental: Operação PUT

Este diagrama detalha a ordem cronológica de chamadas de rede executadas quando o cliente injeta um novo arquivo no sistema de arquivos distribuído, destacando o handoff do plano e a validação ativa do quórum de escrita estável. Note que os bytes vão para UM nó (o ingress), que então replica para as demais réplicas de cada chunk:

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
    |                      |                    |== 7. QUORUM W=2: 2+ replicas OK? SIM ==|
    |                      |<- 8. ConfirmUpload -|                   |                  |
    |                      |   (grava metadados) |                   |                  |
    |<= 9. UploadResult (ok) ===================== |                  |                  |
```

---

### 3.3 Diagrama de Sequência Comportamental: Operação GET

O diagrama abaixo expõe o fluxo de recuperação descentralizada de dados, demonstrando o nó-egress montando o arquivo a partir dos chunks que tem localmente e buscando em peers (via `FetchChunk`) os que faltam, com **failover sequencial** entre as réplicas de cada chunk:

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

## ⚙️ 4. Especificação de Funcionalidades e Comportamento dos Fluxos Internos

Esta seção disseca a mecânica íntima e o comportamento algorítmico de cada um dos fluxos operacionais expostos pelo ecossistema do DFS no Marco 3.

### 4.1 Operação PUT (Injeção Distribuída com Gateway, Handoff, Quórum e Replicação Ativa)

O comando `put` é o fluxo mais complexo do sistema, envolvendo autorização, handoff do plano, fragmentação no gateway, replicação paralela e validação de quórum. Ele opera segundo o seguinte algoritmo em tempo de execução:

1. **Interceptação e Inicialização:** O usuário invoca `python run_cli.py put <caminho_local> <caminho_logico>`. A subcamada de interface CLI intercepta o comando, valida a existência do arquivo local em disco e lê o seu conteúdo, calculando o tamanho total em bytes.
2. **Autorização e Resolução de Roteamento (RequestUpload):** A CLI dispara uma chamada RPC `RequestUpload` para o Coordenador, informando o caminho lógico e o tamanho total. O Coordenador calcula quantos chunks o arquivo terá (`ceil(tamanho / CHUNK_SIZE)`), escolhe o **ingress** entre os nós vivos por round-robin entre arquivos, gera um `upload_id` único (UUID) e pré-computa o `ChunkPlacement` de cada chunk com a **membership canônica** (round-robin determinístico). Registra o upload como pendente e devolve à CLI o `upload_id`, o endereço do ingress e o mapa completo de chunks.
3. **Handoff do Plano (SetUploadPlan):** A CLI repassa ao ingress, via `SetUploadPlan`, o `upload_id`, o tamanho total e o mapa de `ChunkPlacement`. O ingress guarda esse plano em memória (`PlanStore`), indexado pelo `upload_id`. Sem esse passo, o ingress aborta o stream com `FAILED_PRECONDITION` — o gateway precisa saber em quais réplicas gravar cada chunk antes de receber os bytes.
4. **Streaming Direto ao Ingress (UploadFile):** A CLI abre o stream `UploadFile` direto com o ingress, enviando os bytes em pedaços de `STREAM_SIZE` (64 KB) — pequenos de propósito, para manter baixo o uso de memória e ficar muito abaixo do limite default de mensagem do gRPC. **Não há `GRPC_OPTIONS`:** como o transporte é fatiado em 64 KB, nenhuma mensagem chega perto do limite de 4 MB do gRPC, então a sintonia fina de tamanho de mensagem é desnecessária.
5. **Reagrupamento e Fan-out no Ingress:** Conforme os bytes chegam, o ingress os reacumula em chunks oficiais de `CHUNK_SIZE`. Para cada chunk fechado, ele grava localmente caso seja uma das réplicas daquele chunk e dispara, **em paralelo (uma thread por réplica de destino, um canal por destino)**, a RPC `StoreChunk` para as demais réplicas determinadas pelo plano.
6. **Consolidação Física nos Workers:** Ao interceptar o `StoreChunk`, o Worker de destino aciona o seu gerenciador de armazenamento local e grava o bloco de bytes em `data/nodes/nodeX/chunks/<chunk_id>`. A nomenclatura do chunk segue o padrão `<upload_id>_chunk_<índice>`, estável e livre de colisão (UUID por upload).
7. **Avaliação Matemática do Quórum de Escrita ($W=2$):** O ingress arbitra o quórum: um chunk só é considerado gravado se ao menos **duas réplicas** (contando ele próprio, se for réplica) confirmarem. Atingido o quórum, o ingress chama `ConfirmUpload` no coordenador, reportando os chunks efetivamente gravados; o coordenador então persiste os metadados (é só aqui que o arquivo passa a existir para o sistema). O stream se encerra com `UploadResult(ok=true)`. *Observação de estado atual:* a tolerância plena à queda de réplica durante o fan-out é o ponto de robustez sob refinamento contínuo; o caminho feliz e o quórum estão implementados e validados.

### 4.2 Operação GET (Recuperação Descentralizada com Egress por Localidade e Failover Sequencial)

O fluxo de recuperação de dados implementa a disponibilidade de leitura por failover entre réplicas. O fluxo se desdobra nas seguintes etapas:

1. **Requisição de Autorização (RequestDownload):** O usuário executa `python run_cli.py get <caminho_logico> <destino_local>`. A CLI conecta-se via gRPC ao Coordenador invocando `RequestDownload`. O Coordenador efetua uma busca síncrona no índice `metadata_index.json`, captura a entrada lógica do arquivo, o tamanho total e o mapa físico de chunks. **Ele não recalcula placement** — apenas lê o que foi persistido no upload (essa é a regra que viabiliza a elasticidade). Em seguida escolhe o **egress por localidade**: o nó vivo que guarda o maior número de chunks do arquivo, minimizando buscas em peers. Devolve `download_id`, endereço do egress, tamanho total e o mapa de chunks.
2. **Handoff do Plano (SetDownloadPlan):** A CLI repassa ao egress o `download_id`, o tamanho total e o mapa de chunks, que o egress guarda no `PlanStore`. Sem isso, o egress aborta com `FAILED_PRECONDITION`.
3. **Streaming do Egress (DownloadFile) e Failover Sequencial:** A CLI abre o stream `DownloadFile`. Para cada chunk, em ordem de índice, o egress verifica se o tem localmente: se sim, lê do disco; se não, busca em um peer via `FetchChunk`. A busca em peer percorre as réplicas daquele chunk **sequencialmente**: se um peer não tem o chunk ou não responde, tenta o próximo da lista. Isso garante a disponibilidade da leitura enquanto **pelo menos uma** réplica de cada chunk estiver viva. Os bytes são emitidos em pedaços de `STREAM_SIZE`, em ordem estrita.
4. **Remontagem e Escrita Linear em Disco:** A CLI concatena sequencialmente os fluxos recebidos e grava o fluxo binário unificado no disco local do usuário, replicando perfeitamente o arquivo original sem nenhuma corrupção ou perda de integridade (round-trip byte a byte verificado nos testes).

> **Nota de consistência (estado atual vs. objetivo):** o Marco 3 implementa **failover sequencial** na leitura, não um quórum de leitura $R=2$ com comparação de versão. O versionamento por carimbo e o quórum $R=2$ com anti-entropia (que tornariam a consistência forte linearizável, satisfazendo a inequação $W+R>N$) são descritos como evolução planejada (ver seções 5.2, 5.4 e 14).

### 4.3 Operação RM (Remoção Distribuída Comandada e Rotina de Expurgamento Físico)

A deleção de arquivos no DFS opera de forma a garantir que nenhum dado órfão permaneça consumindo os discos do cluster, e é conduzida **pelo coordenador** (não pela CLI), pois é ele a autoridade sobre "este arquivo existe" e o detentor do mapa de chunks. O fluxo comporta-se da seguinte forma:

1. **Invocação e Leitura dos Metadados:** O usuário aciona `python run_cli.py rm <caminho_logico>`. A CLI emite `DeleteFile` ao Coordenador. O Coordenador lê dos metadados o mapa de chunks do arquivo (a fonte sobre onde cada chunk está).
2. **Inversão do Mapa e Disparo Paralelo de Purga:** O Coordenador inverte o mapa de "chunk → réplicas" para "nó → seus chunks" e dispara a deleção em paralelo: **uma thread por nó, um canal por nó**, chamando `DeleteChunk` (em lote) em cada Worker que guarda chunks do arquivo. É o mesmo padrão de fan-out usado na replicação do PUT.
3. **Varredura e Limpeza Física nos Nós:** Cada Worker intercepta a ordem e apaga de seu disco local os chunks indicados. A deleção é *best-effort*: se um nó está morto, seus chunks contam como falha e ficam como órfãos a serem limpos quando o nó voltar (mecanismo `chunks_to_delete` do heartbeat, com campo já no contrato e lógica planejada para o Marco 4).
4. **Remoção dos Metadados (ordem: chunks primeiro, metadados depois):** O Coordenador remove a entrada do índice **depois** de comandar a deleção física — mesmo que algumas deleções tenham falhado, o arquivo deixa de existir logicamente. A ordem (chunks antes, metadados depois) é deliberada: se os metadados fossem apagados primeiro e o processo morresse no meio, os chunks ficariam órfãos sem registro (lixo invisível); no sentido inverso, o pior caso é um metadado apontando para chunk já apagado, o que o GET detecta — prefere-se o erro detectável ao silencioso. A subcamada `Local Storage` ainda remove recursivamente subpastas vazias remanescentes.

### 4.4 Operação LIST (Auditoria e Inspeção do Índice de Metadados)

O comando `list` provê uma janela de auditoria e transparência absoluta sobre o estado lógico atual da infraestrutura distribuída:

1. **Invocação:** O usuário digita `python run_cli.py list`. A CLI aciona o stub `ListFiles` no Coordenador.
2. **Leitura e Consolidação:** O Coordenador lê o `metadata_index.json` e, para cada arquivo, monta uma mensagem `FileEntry` com o caminho lógico, o tamanho total em bytes, a quantidade de chunks e o conjunto de nós que possuem pelo menos um chunk daquele arquivo.
3. **Formatação de Saída:** O Coordenador devolve uma coleção estruturada de mensagens Protobuf (`ListFilesResponse`). A CLI recebe os dados e renderiza, para cada arquivo, uma linha legível no terminal do operador, no formato `[FILE] <caminho>  (<n> chunk(s), <bytes> bytes, nodes=<...>)`.

### 4.5 Operação WORDCOUNT (MapReduce Paralelo Orientado a Localidade) `[Não Feito]`

Projetado para computação distribuída analítica sobre o ecossistema do DFS, o fluxo opera minimizando drasticamente a movimentação de dados na rede:

1. **Disparo Analítico:** O usuário executa `python run_cli.py wordcount <caminho_logico> <termo_busca>`.
2. **Mapeamento de Afinidade por Localidade (*Data Locality*):** O Coordenador recebe a requisição. Em vez de ler o arquivo, ele consulta o mapa de chunks do arquivo. Ele identifica, por exemplo, que o `chunk_0` está no `node1`, o `chunk_1` está no `node2`, e assim por diante.
3. **Despacho Concorrente de Tarefas MapRPC:** O Coordenador aciona o `MapReduce Service` no Control Plane. Ele envia uma chamada gRPC leve (`RunMapTask`) diretamente para o `Node Compute Service` dos nós detentores físicos dos dados, passando o termo de busca.
4. **Processamento Local em Disco (Fase Map):** Cada Worker recebe a ordem de computação. Ele abre os chunks locais persistidos em seu próprio disco rígido, lê os bytes diretamente para a memória local do servidor e executa uma contagem de strings concorrente de alta velocidade. O nó calcula o número de ocorrências localmente e devolve para o Coordenador apenas um número inteiro leve (ex: "node1 encontrou 45 ocorrências"). O arquivo de dados nunca viaja pela rede.
5. **Consolidação Mestre (Fase Reduce):** O Coordenador coleta as respostas numéricas leves vindas da periferia do cluster, executa a função de agregação matemática (soma vetorial de todas as parciais) e devolve o total final consolidado instantaneamente para a CLI do usuário.

---

## 🧩 5. Aprofundamento dos Conceitos Distribuídos e Engenharia de Rede

A robustez do DFS no Marco 3 repousa sobre a aplicação rigorosa de conceitos matemáticos e de engenharia de redes de computadores de sistemas distribuídos modernos.

### 5.1 Topologia do Cluster: $N=5$ Nós e Fator de Replicação $R=3$

O sistema opera com **$N=5$ nós de armazenamento** e **fator de replicação $R=3$**. O fator $R=3$ estipula o nível de redundância física: para cada unidade de informação atômica injetada, coexistem três cópias em nós distintos, garantindo que a perda de até duas réplicas de um chunk não cause perda de dados. A escolha de $N=5$ não é arbitrária: $N=5$ é o **menor número de nós que dá graus de liberdade combinatórios reais** ao round-robin. Com $N=3$ e $R=3$, teríamos `min(R,N)=3`, o que colocaria todo chunk em todos os nós — não haveria o que balancear, justamente o foco do Marco 3. Com $N=5$ e $R=3$, cada chunk ocupa três dos cinco nós, e a janela de réplicas desliza ao longo do arquivo, distribuindo a carga de forma uniforme e exata.

### 5.2 Consistência e o Modelo de Quórum ($W=2$ implementado; $R=2$ planejado)

Para governar a consistência de dados sem depender de protocolos pesados e centralizados de travamento bidirecional (como Two-Phase Locking), o DFS adota o Modelo de Quórum Descentralizado como referência teórica. O alicerce desse modelo baseia-se na inequação fundamental de sistemas distribuídos:

$$W + R > N_{replicas}$$

Onde, no contexto de cada chunk:
- $N_{replicas}$ representa o número de réplicas de um chunk ($=R=3$).
- $W$ representa o Quórum Mínimo de Escrita Estável ($W=2$, **implementado**).
- $R$ representa o Quórum Mínimo de Leitura Consistente ($R=2$, **planejado para o Marco 4**).

Substituindo o objetivo de design ($W=2$, $R=2$, três réplicas), obtém-se $2 + 2 > 3 \implies 4 > 3$. Como a soma dos nós consultados na escrita com os nós consultados na leitura seria estritamente maior do que o número de réplicas, o Princípio da Casa dos Pombos garantiria que a leitura **obrigatoriamente faria interseção com pelo menos um nó** detentor da escrita mais recente, assegurando consistência forte (linearizável) — desde que combinada com versionamento e anti-entropia.

**Estado atual (Marco 3):** o quórum de **escrita** $W=2$ está implementado e validado (um chunk só é dado como gravado com 2 confirmações). A **leitura** ainda usa **failover sequencial** (tenta réplicas em ordem até obter o chunk), e não o quórum $R=2$ com comparação de versão. Em termos de CAP, o sistema prioriza segurança da escrita: se o quórum $W$ não é atingido, a operação falha em vez de gravar de forma inconsistente (preferência por consistência sobre disponibilidade de escrita, quadrante CP).

### 5.3 Placement Determinístico por Round-Robin (substituiu o Hashing)

Para eliminar a necessidade de manter tabelas de roteamento pesadas e centralizadas para cada chunk, o DFS adota um **posicionamento round-robin determinístico** por índice de chunk, que **substituiu** o sharding por hash do Marco 2. A regra é pura e sem estado:

$$\text{réplicas}(chunk_i) = [\, N[(i+0) \bmod N],\ N[(i+1) \bmod N],\ \dots,\ N[(i+R-1) \bmod N]\,]$$

A primeira réplica é o *primary*. Com $N=5$ e $R=3$: o chunk 0 vai para $[node1, node2, node3]$, o chunk 1 para $[node2, node3, node4]$, e assim por diante, com a janela deslizando circularmente. Isso assegura:
- **Determinismo:** qualquer componente calcula a localização teórica de um bloco apenas a partir do seu índice, sem consultar tabelas.
- **Uniformidade exata:** ao contrário do hash (que garante uniformidade apenas estatística), o round-robin produz espalhamento uniforme exato e uma sequência previsível, fácil de auditar na defesa.

**Invariante crítica:** a regra recebe SEMPRE a **membership canônica** (os 5 nós, na ordem fixa), nunca a lista de nós vivos. Se um nó cair e a lista virar 4, o `% N` mudaria e todos os chunks já gravados deixariam de ser encontrados. Liveness afeta de qual réplica se *lê* / para onde se *re-replica*, nunca a fórmula. A função de placement recebe `cluster_size` e falha alto se a lista divergir, blindando contra esse erro. Além disso, o placement é **calculado uma vez no write e persistido**; nenhuma operação posterior recalcula posicionamento — é o que viabiliza a elasticidade (um nó novo entra na membership e passa a receber uploads futuros, sem movimentar dados antigos).

### 5.4 Versionamento Atômico de Arquivos `[Planejado — Marco 4]`

O controle de versionamento operaria como o token imutável de validação para algoritmos de consistência de quórum de leitura. Quando ocorre uma mutação (`put`), um incremento de versão seria carimbado fisicamente junto ao chunk; durante um quórum de leitura $R=2$, um carimbo antigo (de um nó que ficou offline durante um PUT anterior) funcionaria como flag de invalidação, permitindo à CLI identificar e isolar o dado defasado (anti-entropia). **No Marco 3 isso ainda não está implementado** — a leitura usa failover sequencial e os chunks não carregam carimbo de versão. O versionamento e a anti-entropia são parte da evolução planejada para consistência forte linearizável (ver seção 14).

### 5.5 Abandono de Sockets TCP Legados e as Duas Granularidades de Tamanho

O gRPC opera nativamente sobre conexões HTTP/2, trazendo vantagens brutas de performance como multiplexação de streams (várias requisições trafegando simultaneamente por uma única conexão TCP), compressão de cabeçalhos binários e mecanismos eficientes de keep-alive a nível de aplicação.

Por padrão de fábrica, o framework gRPC limita o tamanho de transmissão de mensagens individuais a 4 MB como salvaguarda de estouro de memória. A arquitetura do Marco 3 resolve isso de forma elegante **sem precisar mexer nesse limite**, através da separação de **duas granularidades de tamanho** distintas, que nunca devem ser confundidas:

- **`CHUNK_SIZE = 4 MB` (a chunknização):** unidade de *placement e replicação*. Define em quantos chunks o arquivo é cortado e quantas entradas de metadado e rodadas de replicação ele gera. Chunks grandes reduzem o overhead de metadados (um arquivo de 256 MB gera 64 chunks com 4 MB, em vez de 4096 com 64 KB — redução de 64×) e respondem diretamente ao feedback do Marco 2 sobre escalar o tamanho do chunk.
- **`STREAM_SIZE = 64 KB` (o pedaço de transporte):** quanto a CLI envia por mensagem gRPC ao subir/baixar. É pequeno de propósito: mantém o uso de memória baixo e fica **muito abaixo** do limite default de 4 MB do gRPC.

**Consequência arquitetural:** como todo o tráfego pesado é fatiado em pedaços de `STREAM_SIZE` (64 KB), nenhuma mensagem individual jamais se aproxima do teto de 4 MB do gRPC. Por isso, **a configuração `GRPC_OPTIONS` de aumento de `max_send/receive_message_length` é desnecessária e não é utilizada** neste projeto — o problema clássico de `RESOURCE_EXHAUSTED` por mensagem grande simplesmente não acontece, pois o re-agrupamento em chunks de 4 MB é feito em memória pelo ingress, a partir de um stream de mensagens de 64 KB. Essa decisão (transporte fino + chunk grosso) substitui a antiga abordagem de inflar o limite de mensagem para 64 MB.

---

## 🗂️ 6. Mapeamento e Análise Granular da Estrutura do Projeto

Abaixo encontra-se a árvore de diretórios oficial do ecossistema do DFS no Marco 3, **já no estado integrado**, detalhando minuciosamente a função lógica de cada componente. Note que os artefatos legados do Marco 2 (`sharding.py`, `node_service.py`, `node_client.py`, `file_service.py`) foram **aposentados** na integração e substituídos pela organização por papel técnico abaixo:

```text
MARCO3/
├── .venv/                               # Ambiente virtual isolado Python 3 contendo interpretador e libs.
├── DFS_M3/                              # Diretório mestre que encapsula o código-fonte do pacote DFS.
│   ├── pyproject.toml                   # Especificação de metadados, build-system e empacotamento moderno.
│   ├── requirements.txt                 # Dependências (protobuf e grpcio-tools).
│   │
│   ├── data/                            # Subpastas destinadas à simulação de persistência em disco rígido.
│   │   ├── metadata/                    # Diretório do Control Plane (Coordenador).
│   │   │   └── metadata_index.json      # O banco de dados JSON centralizador de todo o índice lógico do DFS.
│   │   └── nodes/                       # Diretórios simulando discos físicos independentes dos Workers.
│   │       ├── node1/chunks/            # Partição de armazenamento isolada do Nó 1 (chunks/<chunk_id>).
│   │       ├── node2/chunks/            # Partição de armazenamento isolada do Nó 2.
│   │       ├── node3/chunks/            # Partição de armazenamento isolada do Nó 3.
│   │       ├── node4/chunks/            # Partição de armazenamento isolada do Nó 4.
│   │       └── node5/chunks/            # Partição de armazenamento isolada do Nó 5.
│   │
│   ├── dfs/                             # Módulo Python principal que centraliza o core lógico do sistema.
│   │   ├── __init__.py                  # Sinaliza ao interpretador que a pasta é um pacote importável.
│   │   ├── __main__.py                  # Entry point do pacote: viabiliza `python -m dfs <cmd>`.
│   │   ├── config.py                    # Constantes globais (portas, N=5, R=3, CHUNK_SIZE, STREAM_SIZE, heartbeat).
│   │   ├── client.py                    # DataClient (cliente do nó-gateway) + reexporta ControlClient.
│   │   │
│   │   ├── application/                 # Camada de serviços lógicos de regras de negócio de alto nível.
│   │   │   ├── metadata_service.py      # Operações transacionais de leitura/escrita no JSON de metadados mestre.
│   │   │   ├── data_service.py          # DataServicer: nó como ingress (PUT) e egress (GET); fan-out e failover.
│   │   │   ├── replication_service.py   # ReplicationServicer: CRUD de chunks no disco (Store/Fetch/Delete/List).
│   │   │   ├── mapreduce_service.py      # [Não Feito] Master Engine: orquestração analítica por localidade física.
│   │   │   └── node_compute_service.py   # [Não Feito] Worker Engine: varredura local concorrente para tarefas Map.
│   │   │
│   │   ├── cluster/                     # Camada responsável pelo gerenciamento de topologia do cluster.
│   │   │   ├── node_registry.py         # Membership canônica (estática) + estado vivo (heartbeat: ALIVE/SUSPECT/DEAD).
│   │   │   ├── placement.py             # Round-robin determinístico (fonte de verdade do posicionamento).
│   │   │   ├── plan_store.py             # PlanStore + DataPlaneServicer (handoff do plano CLI -> nó).
│   │   │   ├── control_client.py         # Cliente do ControlService (usado por nó e CLI).
│   │   │   └── replication_client.py     # Cliente do ReplicationService (coordenador: deleção; nós: fan-out/fetch).
│   │   │
│   │   ├── interface/                   # Camada exposta para inicialização de processos e interação com o usuário.
│   │   │   ├── cli.py                   # Parsing de argumentos, menu interativo e loop com cliente persistente.
│   │   │   ├── server.py               # Inicializador do servidor gRPC do Coordenador (ControlService, porta 9100).
│   │   │   └── storage_node.py          # Lançador dos Nós (Data + Replication + DataPlane na mesma porta; heartbeat).
│   │   │
│   │   ├── storage/                     # Camada de interação de baixo nível com o hardware hospedeiro.
│   │   │   └── local_storage.py         # I/O de bytes em binário; API por caminho lógico e por chunk_id.
│   │   │
│   │   └── pb/                          # Artefatos de contrato e código compilado pelo Protocol Buffers.
│   │       ├── __init__.py              # Inicialização de pacote para as classes compiladas de rede.
│   │       ├── dfs.proto               # Contrato compartilhado: os três serviços principais (fonte de verdade).
│   │       ├── dataplane.proto          # Contrato interno do plano de dados (handoff do plano de chunks).
│   │       ├── dfs_pb2.py / dfs_pb2_grpc.py            # Gerados a partir de dfs.proto. NÃO editar à mão.
│   │       └── dataplane_pb2.py / dataplane_pb2_grpc.py # Gerados a partir de dataplane.proto. NÃO editar à mão.
│   │
│   ├── scripts/                         # Automações secundárias e rotinas auxiliares do sistema.
│   │   └── start_coordinator.py         # Atalho para subir apenas o coordenador (testes/demonstrações).
│   │
│   ├── tests/                           # Testes manuais e mocks de isolamento (data plane x control plane).
│   │   ├── mocks/                       # mock_coordinator.py e mock_node.py (espelhos para testar cada plano).
│   │   ├── test_local_storage_chunks.py # Round-trip por chunk_id no disco local.
│   │   ├── test_replication_mock.py     # Fan-out e fetch entre réplicas (StoreChunk/FetchChunk).
│   │   ├── test_end_to_end.py           # PUT/GET/LS/DELETE ponta a ponta com coordenador-mock.
│   │   └── test_list_files.py           # ListFiles montando FileEntry corretamente.
│   │
│   └── ARQUITETURA.md                   # Documento de arquitetura "como implementado" (referência viva).
│
├── README.md                            # Este exaustivo manual de engenharia e operações técnicas.
├── run_cluster.py                       # Orquestrador mestre para subir os 5 nós + o coordenador.
└── run_cli.py                           # Ponto de entrada simplificado global para comandos do Data Plane.
```

---

## 🧭 7. O Que Faz Cada Arquivo: Análise Granular Detalhada

Esta seção provê uma autópsia técnica detalhada sobre a responsabilidade funcional interna de cada arquivo que compõe o ecossistema de software do DFS no Marco 3, já no estado integrado.

### 7.1 Arquivos do Diretório Raiz `MARCO3/`

- **`run_cluster.py`:** Atua como o maestro de processos da infraestrutura local. Utilizando o módulo `subprocess` do Python, ele inicializa de forma concorrente e isolada os processos do cluster: as **cinco** instâncias de Nós de Armazenamento (portas 9101 a 9105) e, **em seguida**, a instância do Coordenador (porta 9100). Os nós sobem antes do coordenador de propósito — assim, quando o coordenador começa a processar requisições, os nós já estão prontos; os primeiros heartbeats podem falhar com um aviso inofensivo até o coordenador subir, pois o heartbeat tem retry e os nós entram como ALIVE no ciclo seguinte. O script lê `NODE_ORDER` do `config.py`, então adicionar nós no config faz o runner subi-los automaticamente, sem editar o runner.
- **`run_cli.py`:** Funciona como o portal de entrada para o usuário final. Ele insere `DFS_M3/` no `sys.path`, importa dinamicamente o módulo `dfs.interface.cli` e delega a execução para o seu `main`, permitindo a invocação limpa dos fluxos operacionais (`put`/`get`/`list`/`rm`/`menu`) sem exigir navegação interna de pastas. Sem argumentos, abre o **modo interativo persistente**.

### 7.2 Arquivos do Core Package `DFS_M3/dfs/`

- **`config.py`:** Centraliza as variáveis que ditam o comportamento de todo o cluster: endereço e porta do Coordenador (`127.0.0.1:9100`), porta base dos nós (`9101`, com node1→9101 … node5→9105), o número de nós (`NODE_COUNT = 5`), o fator de replicação (`REPLICATION_FACTOR = 3`), as duas granularidades de tamanho (`CHUNK_SIZE = 4 MB` e `STREAM_SIZE = 64 KB`), os limiares de heartbeat (`HEARTBEAT_INTERVAL = 2s`, `HEARTBEAT_SUSPECT = 4s`, `HEARTBEAT_DEAD = 8s`) e os caminhos de dados. A configuração dos nós é gerada dinamicamente por `build_nodes(NODE_COUNT)`. **Não há `GRPC_OPTIONS`** — por design, o transporte fatiado em 64 KB torna desnecessário inflar o limite de mensagem do gRPC.
- **`client.py`:** Implementa o `DataClient`, o cliente gRPC do nó-gateway. Encapsula os stubs do `DataService` e do `DataPlaneService` na mesma conexão e provê os métodos do plano de dados: `set_upload_plan`/`set_download_plan` (handoff), `upload` (gera o stream de `UploadChunk` em pedaços de `STREAM_SIZE`) e `download` (consome o stream de `DownloadChunk` e concatena os bytes). Reexporta o `ControlClient` para a CLI.
- **`__main__.py`:** Entry point do pacote, viabilizando `python -m dfs <comando>`. Apenas importa e chama o `main` da CLI.

### 7.3 Camada `application/` (Lógica de Serviços)

- **`metadata_service.py`:** É o motor transacional de persistência do Coordenador. Gerencia o `metadata_index.json` em `data/metadata/`, com métodos protegidos por lock para leitura, gravação (`put_file`, que recebe o caminho, o tamanho total em bytes e a lista de chunks com suas réplicas), busca, deleção e listagem. Mantém, por arquivo, o tamanho total, a lista de chunks e um bloco de distribuição (`chunk_count`, `nodes_used`). Não conhece tipos do protobuf — quem chama (o `ConfirmUpload`) converte os `ChunkPlacement` em dicionários simples antes de gravar, mantendo a camada de metadados desacoplada do gRPC.
- **`data_service.py`:** Implementa o `DataServicer`, a interface de streaming da CLI com o nó. No `UploadFile`, o nó age como **ingress**: carrega o plano do `PlanStore` pelo `upload_id`, reagrupa os bytes do stream em chunks de `CHUNK_SIZE`, grava localmente se for réplica, dispara o fan-out paralelo (`StoreChunk`) para as demais réplicas, valida o quórum $W=2$ e chama `ConfirmUpload` no coordenador. No `DownloadFile`, o nó age como **egress**: carrega o plano pelo `download_id`, monta o arquivo lendo chunks locais e buscando os faltantes em peers (`FetchChunk`, com failover sequencial), emitindo o stream em ordem.
- **`replication_service.py`:** Implementa o `ReplicationServicer`, a comunicação nó-a-nó (e o coordenador, para deleção). Expõe `StoreChunk` (recebe um chunk em stream e o grava), `FetchChunk` (lê um chunk local e o emite em pedaços de `STREAM_SIZE`), `DeleteChunk` (apaga um chunk do disco) e `ListChunks` (inventário local para diagnóstico).
- **`mapreduce_service.py` `[Não Feito]`:** Componente de alto nível acoplado ao Coordenador que atuaria como o Master do motor de processamento distribuído, fracionando uma consulta analítica, mapeando a localidade física de cada bloco, disparando tarefas de computação aos Workers e consolidando os retornos (*Reduce*).
- **`node_compute_service.py` `[Não Feito]`:** Serviço operário acoplado ao Worker que interceptaria os sinais do Master do MapReduce, abrindo localmente os chunks no disco, aplicando filtros em memória (*Map*) e devolvendo resultados numéricos leves, sem gerar tráfego pesado de rede.

### 7.4 Camada `cluster/` (Gerenciamento de Topologia)

- **`node_registry.py`:** Gerencia o catálogo de nós com duas responsabilidades rigidamente separadas: a **membership canônica** (estática, lida do config, sempre na mesma ordem — é o que o placement consome) e o **estado vivo** (dinâmico, atualizado por `register_node` e `record_heartbeat`). Classifica cada nó como ALIVE, SUSPECT ou DEAD pelo tempo de silêncio desde o último batimento, num cálculo preguiçoso feito na hora da consulta, sem thread de fundo. Expõe `canonical_members()` (para placement) e `alive_members()` (para roteamento de ingress/egress).
- **`placement.py`:** Contém a função pura de distribuição round-robin determinística do ecossistema. `replicas_for_chunk(chunk_index, nodes, R, cluster_size)` devolve as réplicas de um chunk pela regra `(i+offset) % N`; `primary_replica` devolve só o primary; `ingress_for_file` escolhe o ingress por round-robin entre arquivos. Recebe `cluster_size` explicitamente como blindagem: se a lista divergir da membership canônica, estoura em vez de calcular errado. Substitui o `sharding.py` por hash do Marco 2.
- **`plan_store.py`:** Implementa o `PlanStore` (mapa em memória, protegido por lock, de `upload_id`/`download_id` → plano de chunks, com limpeza após a operação) e o `DataPlaneServicer`, que atende `SetUploadPlan`/`SetDownloadPlan`. É a peça que materializa o handoff do plano da CLI para o nó, descrito na seção 6 do guia de arquitetura.
- **`control_client.py`:** Cliente gRPC do `ControlService`, usado tanto pelo **nó** (`register`, `heartbeat`, `confirm_upload`) quanto pela **CLI** (`request_upload`, `request_download`, `delete_file`, `list_files`). Endereço default lido do `config.py`.
- **`replication_client.py`:** Reúne os dois clientes do `ReplicationService` na mesma camada: as funções de nível de coordenador (`delete_node_chunks`/`delete_one_chunk`, usadas no `DeleteFile` para comandar a deleção física, reusando um canal por nó) e a classe `ReplicationClient` (usada pelos nós no fan-out do PUT via `store_chunk` e no failover do GET via `fetch_chunk`). Tudo trafega em pedaços de `STREAM_SIZE`.

### 7.5 Camada `interface/`, `storage/` e Artefatos Compilados `pb/`

- **`cli.py`:** Interface de linha de comando. Faz o parsing dos comandos (`put`/`get`/`list`/`rm`/`menu`), exibe o menu interativo formatado e mantém o loop de sessão. Implementa o fluxo de **três chamadas** do Marco 3 e mantém um **cliente persistente** (um único `ControlClient` reusado em toda a sessão interativa; o `DataClient` é aberto por operação, pois o nó-gateway varia por arquivo). Envolve cada comando em tratamento de `grpc.RpcError` para que a sessão não morra se um nó/coordenador estiver fora.
- **`server.py`:** Inicializador do servidor gRPC do Coordenador. Implementa o `ControlServiceServicer` com as sete RPCs do plano de controle (`RegisterNode`, `Heartbeat`, `RequestUpload`, `ConfirmUpload`, `RequestDownload`, `DeleteFile`, `ListFiles`), usando o `NodeRegistry`, o `placement.py`, o `MetadataService` e o `replication_client`. Registra **`ControlServiceServicer`** (não mais o legado `DFSService`) e escuta em `127.0.0.1:9100`.
- **`storage_node.py`:** Lançador de um Nó de Armazenamento. Sobe, na **mesma porta**, os três serviços do nó (`DataService`, `ReplicationService`, `DataPlaneService`) e dispara, em background, o registro e o heartbeat junto ao coordenador (a cada `HEARTBEAT_INTERVAL`, com o block report do inventário de chunks). Uso: `python -m dfs.interface.storage_node --node-id node1`.
- **`local_storage.py`:** Camada de I/O de baixo nível. Mantém a API legada por caminho lógico (`put`/`get`/`delete`/`list_files`) e adiciona a API por `chunk_id` (`store_chunk`/`read_chunk`/`has_chunk`/`delete_chunk`/`list_chunk_ids`), gravando cada chunk em `chunks/<chunk_id>` **sem extensão** (para casar com o regex `_chunk_\d+$` do observer). Garante isolamento de caminhos contra *path traversal* e remove subpastas vazias após deleções.
- **`dfs.proto`:** Contrato definitivo IDL do ecossistema (pacote `dfs.v1`), fonte de verdade dos três serviços principais (`ControlService`, `DataService`, `ReplicationService`) e de todas as mensagens. **Não deve ser editado sem coordenação prévia da dupla.**
- **`dataplane.proto`:** Contrato interno do plano de dados (pacote `dfs.dataplane`). Declara o `DataPlaneService` (`SetUploadPlan`/`SetDownloadPlan`) e reusa `ChunkPlacement` e `Ack` do `dfs.proto` via import, sem redefinir nada. Separado de propósito, para manter o contrato compartilhado e os stubs do coordenador intocados.
- **`dfs_pb2.py` / `dfs_pb2_grpc.py` / `dataplane_pb2.py` / `dataplane_pb2_grpc.py`:** Código gerado automaticamente pelo compilador `grpc_tools.protoc` a partir dos `.proto`. Contêm as classes de mensagens e os stubs/servicers de rede. **Não devem ser editados manualmente** — sempre regenerar (ver seção 8, Step 4).

---

## 🚀 8. Guia de Execução Operacional Detalhado

Toda a preparação de ambiente virtual, instalação de dependências core, compilação de stubs e execução da infraestrutura distribuída deve ser realizada obrigatoriamente a partir do diretório raiz `MARCO3/`.

### Step 1: Provisionar o Ambiente Virtual Isolado (VENV)
Crie o ambiente virtual Python 3 para garantir o completo isolamento das bibliotecas do projeto:
```bash
python -m venv .venv
```

### Step 2: Ativar o Ambiente Virtual baseando-se no Sistema Operacional
Ative a `venv` de acordo com as especificidades do seu terminal de comandos e sistema operacional:
- **Linux / macOS (Bash/Zsh):**
  ```bash
  source .venv/bin/activate
  ```
- **Windows (PowerShell):**
  ```bash
  .venv\Scripts\Activate.ps1
  ```
- **Windows (Prompt de Comando CMD clássico):**
  ```bash
  .venv\Scripts\activate.bat
  ```
- **Windows rodando VS Code com terminal Git Bash:**
  ```bash
  source .venv/Scripts/activate
  ```

### Step 3: Instalar as Dependências Core do Ecossistema
Com a sua `venv` devidamente ativada no terminal, execute o gerenciador de pacotes para sanar as dependências obrigatórias de rede e compilação do gRPC:
```bash
pip install -r DFS_M3/requirements.txt
```

### Step 4: Compilação Manual do Contrato IDL (Protobuf / gRPC)
Sempre que um dos arquivos de especificação (`DFS_M3/dfs/pb/dfs.proto` ou `DFS_M3/dfs/pb/dataplane.proto`) sofrer qualquer modificação, mude o escopo para a raiz do pacote (`DFS_M3/`) e recompile **os dois** `.proto`. É **obrigatório** usar `-I=.` (e não `-I=dfs/pb`), pois é isso que faz o `protoc` gerar o import qualificado `from dfs.pb import dfs_pb2`; com `-I=dfs/pb` ele geraria `import dfs_pb2` (plano), que quebra em tempo de execução com `ModuleNotFoundError`:
```bash
cd DFS_M3
python -m grpc_tools.protoc -I=. --python_out=. --grpc_python_out=. dfs/pb/dfs.proto
python -m grpc_tools.protoc -I=. --python_out=. --grpc_python_out=. dfs/pb/dataplane.proto
cd ..
```
*(O `dataplane_pb2` reusa `dfs.v1.ChunkPlacement` e `dfs.v1.Ack` via `from dfs.pb import dfs_pb2`; por isso a ordem e o `-I=.` importam. Nunca edite os arquivos `*_pb2*.py` à mão.)*

### Step 5: Inicializar os Diretórios Físicos do Simulador de Discos
Garanta a existência prévia da árvore de diretórios necessária para simular o isolamento físico dos storages locais dos cinco nós e da pasta de metadados mestre do Coordenador (o sistema também cria as pastas sob demanda, mas é bom garantir):
```bash
mkdir -p DFS_M3/data/metadata
mkdir -p DFS_M3/data/nodes/node1 DFS_M3/data/nodes/node2 DFS_M3/data/nodes/node3
mkdir -p DFS_M3/data/nodes/node4 DFS_M3/data/nodes/node5
```
> **Dica de higiene:** ao mudar `NODE_COUNT` ou ao migrar de uma versão antiga dos metadados, apague `DFS_M3/data/metadata/*` e `DFS_M3/data/nodes/*` antes de subir o cluster — dados gravados sob um placement diferente ficam inacessíveis e poluem o `list`.

### Step 6: Lançar e Subir o Cluster gRPC Completo Online
Para colocar toda a infraestrutura distribuída online em uma única chamada de console, invoque o script centralizador de subprocessos:
```bash
python run_cluster.py
```
*Atenção: este terminal passará a cuspir logs concorrentes unificados gerados simultaneamente pelo Coordenador e pelos 5 nós de armazenamento ativos. Mantenha esta janela aberta e intocada durante toda a sua simulação de testes. É normal ver um aviso inicial de heartbeat falhando até o coordenador subir (os nós sobem antes); o retry resolve no ciclo seguinte.*

### Step 7: Interagir com o DFS via Interface CLI
Abra uma janela de terminal completamente independente, garanta a ativação prévia da sua `venv` e execute comandos operacionais utilizando o lançador unificado do Data Plane:
```bash
python run_cli.py <comando> [argumentos]
```

---

## 🧪 9. Exemplos de Uso Prático e Simulações de Cenários

### 9.1 Preparar um Arquivo Local para Testes de Transmissão
Gere um arquivo textual contendo dados arbitrários na raiz do projeto para servir de cobaia de I/O distribuído:
```bash
echo "Sistemas distribuidos e replicados utilizando gateway e round-robin gRPC Marco 3" > DFS_M3/teste.txt
```

### 9.2 Injetar o Arquivo Local no Ecossistema DFS (PUT)
Envie o arquivo local para uma rota virtual parametrizada dentro da árvore lógica do sistema de arquivos distribuído:
```bash
python run_cli.py put DFS_M3/teste.txt documentos/financeiro/dados.txt
```

### 9.3 Auditar o Índice e Metadados Globais do Cluster (LIST)
Consulte o estado de registro lógico atualizado para checar a existência, tamanho consolidado, número de chunks e nós que guardam o arquivo injetado:
```bash
python run_cli.py list
```

### 9.4 Recuperar o Arquivo Distribuído via Egress e Failover (GET)
Efetue a descarga descentralizada dos blocos diretamente do nó-egress (que busca em peers o que não tiver localmente), remontando o arquivo de forma limpa em disco:
```bash
python run_cli.py get documentos/financeiro/dados.txt copia_recuperada.txt
```

### 9.5 Disparar Processamento Analítico Local por Localidade `[Não Feito]` (WORDCOUNT)
Acione a rotina computacional do MapReduce para executar busca e contagem paralela de strings diretamente nos discos dos Workers:
```bash
python run_cli.py wordcount documentos/financeiro/dados.txt "distribuidos"
```

### 9.6 Expurgar Arquivo Físico e Limpar Discos dos Nós (RM)
Remova logicamente o arquivo do índice e dispare ordens de destruição física de chunks (comandadas pelo coordenador, em paralelo) em todas as réplicas do cluster:
```bash
python run_cli.py rm documentos/financeiro/dados.txt
```

### 9.7 Entrar no Modo Interativo Persistente de Alta Velocidade da CLI
Invoque a interface sem passar argumentos para iniciar o loop de sessão interativa do DFS:
```bash
python run_cli.py
```
*Vantagem arquitetural crucial: este modo mantém o canal gRPC com o coordenador instanciado em cache na sessão ativa do terminal, eliminando o overhead temporal de reabrir conexão e refazer o handshake HTTP/2 a cada comando sequencial digitado. Dentro da sessão, os comandos `put`, `get`, `list`, `rm` e `menu`/`help` ficam disponíveis no prompt `dfs>`.*

---

## 🔍 10. Detalhamento Técnico Profundo dos Fluxos de Operação (Traces Lógicos)

Esta seção documenta o encadeamento detalhado de eventos lógicos estruturados que guiam a execução interna do sistema do DFS em cada cenário operacional.

### 10.1 Rastreamento Completo do Fluxo PUT (Escrita Distribuída via Gateway)

```text
[Operador CLI] ---> Executa comando put teste.txt docs/documento.txt
  |
  +---> CLI le o arquivo local (ex: 8MB) e calcula tamanho total em bytes
  |
  +---> CLI emite gRPC [RequestUpload(path, size)] ---> [Coordenador (9100)]
          |
          +---> Coordenador calcula total_chunks = ceil(size / 4MB) -> 2 chunks
          +---> Escolhe o INGRESS entre os nos vivos (round-robin entre arquivos)
          +---> Pre-computa o placement round-robin com a membership canonica:
          |       - chunk_0 -> [node1, node2, node3]
          |       - chunk_1 -> [node2, node3, node4]
          +---> Gera upload_id (UUID), registra upload pendente
          |
[Coordenador] ---> Devolve [upload_id, ingress, mapa de ChunkPlacement] ---> [CLI]
  |
  +---> CLI emite [SetUploadPlan(upload_id, total, chunks)] ---> [Ingress]
  |       (ingress guarda o plano no PlanStore; sem isso, abortaria com FAILED_PRECONDITION)
  |
  +===> CLI abre stream [UploadFile] -> Ingress, enviando bytes em pedacos de 64KB (STREAM_SIZE)
  |       |
  |       +---> Ingress reagrupa os bytes em chunks oficiais de 4MB (CHUNK_SIZE)
  |       |
  |       +===> FECHAMENTO DO CHUNK 0 (replicas: node1, node2, node3):
  |       |       - Ingress grava local se for replica deste chunk
  |       |       - Fan-out paralelo [StoreChunk] -> node1, node2, node3 (uma thread/canal por destino)
  |       |       - node3 falha (TIMEOUT). node1 e node2 confirmam OK.
  |       |       - QUORUM W=2: 2 confirmacoes -> chunk 0 consolidado.
  |       |
  |       +===> FECHAMENTO DO CHUNK 1 (replicas: node2, node3, node4):
  |       |       - Fan-out paralelo [StoreChunk] -> node2, node3, node4
  |       |       - todas confirmam OK. QUORUM W=2 atingido.
  |
  +---> Ingress emite [ConfirmUpload(upload_id, chunks gravados, total)] ---> [Coordenador]
  |       (Coordenador converte ChunkPlacement -> dict e grava no metadata_index.json)
  |       (e SO AGORA o arquivo passa a existir para o sistema: aparece no LIST, encontravel no GET)
  |
[Ingress] ---> Fecha o stream com [UploadResult(ok=true)] ---> [CLI]
  |
[CLI] ---> Imprime "upload concluído" e libera o plano do PlanStore.
```

---

### 10.2 Rastreamento Completo do Fluxo GET (Leitura com Egress por Localidade e Failover Sequencial)

```text
[Operador CLI] ---> Executa comando get docs/documento.txt copia.txt
  |
  +---> CLI emite [RequestDownload(logical_path)] ---> [Coordenador (9100)]
          |
          +---> Coordenador busca no metadata_index.json (NAO recalcula placement)
          +---> Le o tamanho total e o mapa de chunks/replicas persistido
          +---> Escolhe o EGRESS por LOCALIDADE: no vivo com mais chunks do arquivo
          +---> Gera download_id (UUID)
          |
[Coordenador] ---> Devolve [download_id, egress, total, mapa de chunks] ---> [CLI]
  |
  +---> CLI emite [SetDownloadPlan(download_id, total, chunks)] ---> [Egress]
  |       (egress guarda o plano no PlanStore)
  |
  +===> CLI abre stream [DownloadFile(download_id)] -> Egress
  |       |
  |       +---> Para cada chunk, em ordem de indice:
  |       |       - Egress tem o chunk localmente? -> le do disco
  |       |       - Nao tem? -> FetchChunk em um peer (failover sequencial pela lista de replicas)
  |       |           * se o peer nao tem / nao responde -> tenta o proximo da lista
  |       |           * disponivel enquanto AO MENOS UMA replica do chunk estiver viva
  |       |       - Emite os bytes do chunk em pedacos de 64KB (STREAM_SIZE), em ordem
  |
  +---> CLI concatena os DownloadChunk recebidos e grava em copia.txt
  |
[CLI] ---> "Arquivo baixado (N bytes) -> salvo em copia.txt" (round-trip byte a byte identico).
```

---

### 10.3 Rastreamento Completo do Fluxo RM (Exclusão Comandada e Descentralizada)

```text
[Operador CLI] ---> Executa comando rm docs/documento.txt
  |
  +---> CLI emite [DeleteFile(logical_path)] ---> [Coordenador (9100)]
          |
          +---> Coordenador le os metadados do arquivo (mapa chunk -> replicas)
          +---> Inverte o mapa para "no -> seus chunks"
          +---> Dispara a delecao em PARALELO (uma thread por no, um canal por no):
          |       |
          |       |--- [DeleteChunk em lote] -> node1 -> apaga seus chunks, devolve (ok, falhas)
          |       |--- [DeleteChunk em lote] -> node2 -> apaga seus chunks
          |       |--- [DeleteChunk em lote] -> node3 -> NO MORTO: chunks contam como falha (best-effort)
          |       |--- [DeleteChunk em lote] -> node4 -> apaga seus chunks
          |
          +---> Remove a entrada do metadata_index.json (chunks primeiro, metadados depois)
          |       (orfaos em nos mortos serao limpos no Marco 4 via chunks_to_delete)
          |
[Coordenador] ---> Devolve [Ack(ok, "N replicas apagadas, M falhas (best-effort)")] ---> [CLI]
  |
[CLI] ---> Imprime a mensagem de confirmacao da exclusao logica global.
```

---

### 10.4 Rastreamento Completo do Fluxo WORDCOUNT (Computação MapReduce Paralela) `[Não Feito]`

```text
[Operador CLI] ---> Executa comando wordcount docs/documento.txt "concorrência"
  |
  +---> CLI emite requisição gRPC [LaunchMapReduceRequest] ---> [Coordenador (9100)]
          |
          +---> Coordenador (Master) interroga os metadados para mapear a localidade dos chunks
          +---> Ativa o MapReduce Service e despacha, por LOCALIDADE DE DADOS:
          |       |--- [RunMapTask] -> node detentor do chunk_0 (termo "concorrência")
          |       |--- [RunMapTask] -> node detentor do chunk_1 (termo "concorrência")
          |
          +===> FASE MAP (nos Workers): cada no abre seus chunks locais, conta o termo em RAM,
          |       devolve apenas um inteiro leve (overhead de rede zero para os dados).
          |
          +===> FASE REDUCE (no Coordenador): soma as parciais -> total consolidado.
          |
[Coordenador] ---> Retorna [LaunchMapReduceResponse(total)] ---> [CLI]
  |
[CLI] ---> Renderiza o resultado métrico final da computação distribuída paralela.
```

---

## 🛠️ 11. Decisões de Projeto e Alinhamento Técnico Perante Teorema CAP

A engenharia de software aplicada no design do DFS no Marco 3 foi norteada por decisões arquiteturais rígidas de alinhamento com os teoremas clássicos de computação distribuída, especificamente o **Teorema CAP (Consistency, Availability, Partition Tolerance)** de Eric Brewer.

- **Escolha pelo Quadrante CP (Consistency and Partition Tolerance):** O design prioriza a Consistência da escrita e a Tolerância a Partições de Rede. Diante de uma falha que inviabilize o **quórum de escrita $W=2$** de um chunk, o sistema prefere **falhar a operação de forma segura** a gravar um chunk com replicação insuficiente, evitando estados divergentes. A leitura, por sua vez, prioriza disponibilidade dentro do que os dados persistidos permitem, via failover sequencial entre réplicas — enquanto ao menos uma réplica de cada chunk estiver viva, o GET é servido. *(O fechamento pleno da consistência forte linearizável, com quórum de leitura $R=2$, versionamento e anti-entropia satisfazendo $W+R>N$, é a evolução planejada — ver seções 5.2, 5.4 e 14.)*
- **Remoção Absoluta de Gargalos por Descentralização de I/O:** Ao abdicar do modelo clássico centralizado de proxying de arquivos (onde os bytes passariam obrigatoriamente pelo coordenador), o DFS adota o **modelo gateway**: os bytes fluem da CLI para um nó (ingress/egress) e entre nós, diretamente. O Coordenador gerencia estritamente o plano de controle, viabilizando que o plano de dados flua na periferia do ecossistema e que a vazão agregada do cluster cresça com a adição de nós (cada novo nó adiciona banda de I/O). É o mesmo princípio do NameNode/DataNodes do HDFS.
- **Elasticidade sem Movimentação de Dados (Placement no Write, Persistido):** Como o placement é decidido uma única vez no upload e gravado nos metadados, a entrada de um nó novo na membership canônica é uma operação O(1) no coordenador, com **zero movimentação de dados existentes**. Uploads futuros já incluem o nó novo; uploads antigos permanecem onde estão, pois seus metadados não mudam. Recalcular posicionamento a cada mudança de membership exigiria mover dados em massa — exatamente o que o modelo "decide no write, metadados são a verdade" evita.
- **Otimização de Banda por Localidade de Dados:** Na leitura, o egress é escolhido como o nó vivo que já guarda o maior número de chunks do arquivo, minimizando as buscas em peers (`FetchChunk`) e o tráfego inter-nós. A engine de MapReduce orientada a localidade `[Não Feito]` levaria esse princípio adiante, enviando a computação aos dados em vez de trafegar os dados até a computação.

---

## 🧪 12. Critérios Técnicos Atendidos no Marco 3

- Implementação completa e funcional de rede distribuída nativa rodando sobre canais multiplexados gRPC e transporte estável HTTP/2.
- Definição estrita, tipada e agnóstica de contratos de interface baseada em Protocol Buffers (IDL), em **dois** contratos: `dfs.proto` (compartilhado, três serviços) e `dataplane.proto` (interno, handoff do plano).
- Descentralização real de tráfego pesado com isolamento arquitetural absoluto entre Control Plane e Data Plane (**modelo gateway**: o coordenador nunca toca em bytes).
- Mecanismo funcional de **Replicação Ativa** síncrona/concorrente (fan-out paralelo) com fator de replicação fixo $R=3$ sobre $N=5$ nós.
- **Quórum de Escrita Estável $W=2$** implementado e validado no fan-out do PUT.
- **Leitura por failover sequencial** entre réplicas, garantindo disponibilidade enquanto ao menos uma réplica de cada chunk estiver viva (quórum de leitura $R=2$ com versionamento/anti-entropia: planejado para o Marco 4).
- **Placement round-robin determinístico** por índice de chunk (substituindo o sharding por hash), calculado uma vez no write e persistido (viabilizando elasticidade sem movimentação de dados).
- **Supervisão de nós por heartbeat** com classificação ALIVE / SUSPECT / DEAD (limiares 2/4/8 s) e block report, usada para roteamento de ingress/egress.
- **Handoff do plano de chunks** (`SetUploadPlan`/`SetDownloadPlan`) via contrato interno, mantendo o contrato compartilhado intocado.
- **Deleção comandada pelo coordenador**, em paralelo (uma thread por nó), best-effort, com ordem segura (chunks antes, metadados depois).
- Cliente interativo estável operando com **cache persistente do canal gRPC** do coordenador para mitigação de latência de handshakes.
- Rotinas de limpeza física de storage local nos nós com remoção recursiva de diretórios vazios residuais.
- **Integração ponta a ponta validada:** `put`/`get`/`list`/`rm` com verificação byte a byte do round-trip, contra coordenador real e cinco nós reais.

---

## ⚠️ 13. Matriz de Tratamento de Falhas, Exceções e Resolução de Erros Críticos

- **Esquecimento de Recompilação do Protobuf:** Caso se altere a assinatura de mensagens ou se adicione uma RPC em `dfs.proto`/`dataplane.proto` e se esqueça de regenerar os stubs, o interpretador disparará exceções de atributo (`AttributeError: module 'dfs_pb2' has no attribute...`). **Resolução:** recompile **os dois** `.proto` com o comando do Step 4, sempre com `-I=.` a partir de `DFS_M3/`.
- **Imports Quebrados nos Stubs (`ModuleNotFoundError: No module named 'dfs_pb2'`):** Sintoma clássico de ter compilado com `-I=dfs/pb` em vez de `-I=.`. O `protoc` gerou um import plano (`import dfs_pb2`) que não resolve em runtime. **Resolução:** recompile com `-I=.` a partir de `DFS_M3/`, conforme o Step 4, para gerar `from dfs.pb import dfs_pb2`.
- **Falta do Handoff do Plano (`FAILED_PRECONDITION: sem plano para upload_id/download_id`):** O nó-gateway recebeu o stream sem ter recebido antes o `SetUploadPlan`/`SetDownloadPlan`. **Resolução:** use a CLI oficial (`run_cli.py`), que faz as três chamadas na ordem correta; scripts manuais que pulam o handoff sempre falharão aqui.
- **Lixo de Metadados de Modelo Antigo no `list` (ex.: arquivo de poucos MB aparecendo com dezenas de chunks):** Sinal de `metadata_index.json` e/ou `data/nodes/` remanescentes de um placement antigo (hash/Marco 2) misturados ao novo. **Resolução:** pare o cluster e limpe `DFS_M3/data/metadata/*` e `DFS_M3/data/nodes/*` antes de testar (ver Step 5).
- **Desencontro de Parâmetro no `ConfirmUpload`/`put_file`:** Se o `ConfirmUpload` chamar `put_file` com um nome de argumento que a assinatura não declara (ex.: `total_size_bytes`), o coordenador estoura `unexpected keyword argument`. **Resolução:** alinhe o nome do parâmetro entre quem chama (`ConfirmUpload`) e a definição (`put_file`); o data plane até grava os chunks antes de o erro aparecer, então o problema é exclusivamente do contrato interno do coordenador.
- **Falha de Inicialização por Portas Ocupadas (`Address already in use`):** Processos zumbis de execuções passadas podem reter os sockets do cluster (portas 9100 a 9105). Caso ocorra erro de *bind* na subida, finalize os subprocessos remanescentes do seu sistema operacional (ex.: `pkill -f python`, ou pelo gerenciador de tarefas no Windows).
- **Aviso de Heartbeat na Subida (`RegisterNode falhou (coordenador no ar?)`):** Como os nós sobem **antes** do coordenador no `run_cluster.py`, os primeiros batimentos podem falhar até o coordenador iniciar. **Resolução:** nenhuma — é esperado e inofensivo; o retry do heartbeat coloca os nós como ALIVE no ciclo seguinte.
- **Inabilidade de Atingir o Quórum de Escrita:** Se réplicas demais caírem durante um PUT, a escrita de um chunk pode não atingir $W=2$ e a operação falha de forma segura, em vez de gravar de modo inconsistente — comprovação prática da prioridade de consistência (quadrante CP) na escrita.

---

## 📌 14. Próximos Passos e Desafios de Engenharia de Sistemas Distribuídos

A consolidação do Marco 3 pavimenta o caminho e assenta as bases tecnológicas definitivas para as seguintes expansões em marcos subsequentes do projeto:

- **Re-replicação Automática (Self-Healing):** ao detectar a morte definitiva de um nó, recalcular as réplicas faltantes e comandar os nós sobreviventes a restaurar o fator $R=3$ copiando os chunks remanescentes entre si, sem intervenção humana. O `NodeRegistry` (DEAD) e o block report do heartbeat já fornecem a base de informação.
- **Limpeza de Chunks Órfãos (`chunks_to_delete`):** ligar a lógica, no coordenador, que detecta chunks que existem fisicamente num nó mas não estão nos metadados (ou que sobreviveram a um nó morto durante um RM) e os manda apagar no próximo heartbeat — o campo já existe no contrato.
- **Consistência Forte de Leitura (Versionamento + Quórum $R=2$ + Anti-entropia):** carimbar versão por chunk, ler de um quórum $R=2$, comparar versões e descartar/“curar” réplicas defasadas — fechando a inequação $W+R>N$ para linearizabilidade, evoluindo o atual failover sequencial.
- **Protocolos de Consenso para Alta Disponibilidade do Coordenador (ex.: Raft):** eliminar o Coordenador como ponto único de falha através de um anel de masters sob consenso, com eleição de líder e replicação de metadados.
- **Rebalanceamento Automático de Carga de Disco:** analisar a ocupação física de cada nó e mover blocos de storages sobrecarregados para nós ociosos, mantendo a homogeneidade do cluster dinamicamente.
- **MapReduce por Localidade (`wordcount`):** implementar o `mapreduce_service` e o `node_compute_service` hoje marcados como `[Não Feito]`, enviando computação aos dados.
- **Validação Empírica de `CHUNK_SIZE` por Benchmark:** medir throughput e latência variando o tamanho do chunk, documentando os trade-offs (Marco 5).

---

## 👨‍💻 15. Observações Finais de Restrição

- O ecossistema opera sob **placement round-robin determinístico por índice de chunk** (não mais hashing), dispensando bancos de dados relacionais para mapeamento físico de blocos; a localização teórica de qualquer chunk é calculável a partir do seu índice e da membership canônica.
- O placement EXIGE a membership canônica (os $N=5$ nós, na ordem fixa) — passar a lista de nós vivos no lugar dela deslocaria o `% N` e tornaria chunks já gravados inacessíveis; a função de placement se blinda contra isso exigindo `cluster_size`.
- As duas granularidades de tamanho (`CHUNK_SIZE = 4 MB` para placement/replicação e `STREAM_SIZE = 64 KB` para transporte) **nunca devem ser confundidas**; é essa separação que dispensa o `GRPC_OPTIONS`.
- O caminho local do arquivo de origem na máquina hospedeira deve existir com permissões de leitura válidas antes de invocar o `put` (a CLI resolve caminhos relativos a partir do diretório onde é executada).
- O caminho virtual de um arquivo dentro da árvore lógica do DFS pode ser completamente independente do nome ou da localização real do arquivo físico na máquina do usuário, operando como uma camada de abstração pura.
- Mudanças no `dfs.proto`, no `placement.py` ou no `ARQUITETURA.md` exigem combinação prévia entre a dupla e entram na `main` via PR.

---

## 👨‍💻 16. Autores

- **Higor Ferreira Silva** — Matrícula: 202201635 — Plano de Dados (DataService, ReplicationService, DataPlaneService, PlanStore, LocalStorage, CLI de dados).
- **Vitória Mendonça** — Matrícula: 202004699 — Plano de Controle (ControlService/coordenador, NodeRegistry, placement, metadados, deleção comandada).

Disciplina: Sistemas Distribuídos 1 — Prof. Vagner José Sacramento Rodrigues — Engenharia de Computação, EMC/UFG.
