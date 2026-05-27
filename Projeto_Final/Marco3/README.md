# Distributed File System (DFS) — Marco 3
## Manual de Arquitetura, Engenharia de Sistemas Distribuídos e Documentação Técnica Operacional

---

## 📌 1. Visão Geral Expandida

Este projeto acadêmico e de engenharia de software consiste na implementação de uma infraestrutura robusta de **Sistema de Arquivos Distribuído (DFS - Distributed File System)** desenvolvida integralmente na linguagem Python. Historicamente, o sistema evoluiu a partir de uma arquitetura legada (Marcos 1 e 2) baseada em sockets TCP brutos (puros), os quais dependiam de um mecanismo de controle de fluxo manual e arbitrário denominado *framing por tamanho* (implementado via `frame.py` e `protocol.py`). No **Marco 3**, o ecossistema sofre uma completa disrupção arquitetural, abandonando as amarras do gerenciamento manual de buffers de rede de baixo nível e migrando de forma definitiva para uma infraestrutura de rede moderna, nativa, assíncrona e multiplexada baseada em **gRPC** (Remote Procedure Calls) rodando sobre a camada de transporte **HTTP/2**, utilizando o **Protocol Buffers (Protobuf)** tanto como linguagem de definição de interface (IDL - Interface Definition Language) estrita quanto como motor de serialização binária ultraotimizada em tempo de execução (runtime).

No contexto do **Marco 3**, a topologia lógica e física do cluster deixa de operar sob a ótica de um Sharding simples e estático, despido de qualquer tolerância a falhas, e transmuta-se em um ecossistema distribuído de alta complexidade, descentralizado, coordenado e altamente tolerante a partições de rede. O sistema passa a ser estruturado com base nos seguintes pilares fundamentais:

- **Múltiplos Nós de Armazenamento Coordenados e Independentes (Workers):** Instâncias autônomas que gerenciam seus próprios discos rígidos virtuais, respondendo a chamadas de I/O de rede sem conhecimento centralizado do restante do cluster.
- **Particionamento e Fragmentação de Arquivos em Chunks Líquidos:** Arquivos volumosos inseridos no ecossistema não são armazenados como blocos monolíticos. O sistema realiza o fatiamento lógico do arquivo em pedaços binários de tamanho fixo configurável (`CHUNK_SIZE`), otimizando o paralelismo e a distribuição espacial da carga de disco.
- **Replicação Ativa de Dados com Fator Fixo ($N=3$):** Para cada bloco lógico (*chunk*) gerado pelo processo de fragmentação, o ecossistema calcula e propaga de forma síncrona/concorrente três réplicas idênticas em servidores físicos totalmente isolados, mitigando riscos de perda de dados decorrentes de falhas de hardware.
- **Consistência Forte via Modelo de Quórum Estrito ($W=2, R=2$):** O DFS adota salvaguardas matemáticas estritas para garantir que leituras de dados obsoletos (*stale reads*) sejam impossibilitadas em tempo de execução, assegurando que o cliente sempre obtenha o estado mais legítimo e recente da informação.
- **Controle de Versionamento Global e Atômico:** Cada mutação de estado de um arquivo dispara um incremento atômico em um contador global de versões gerenciado pelo plano de controle. Esse metadado de versão é carimbado fisicamente junto aos blocos de bytes nos discos dos Workers, servindo como token definitivo de auditoria.
- **Separação Estrita entre Control Plane (Plano de Controle) e Data Plane (Plano de Dados):** Descentralização radical do tráfego de rede do cluster. O nó centralizador (Coordenador) é completamente removido do fluxo de passagem de bytes pesados, atuando única e exclusivamente na resolução lógica de rotas, metadados e quóruns.
- **Motor de Computação Distribuída Orientado a Localidade de Dados (MapReduce) `[Não Feito]`:** Acoplamento de uma engine de processamento paralelo que envia a computação em direção ao local físico onde os blocos de dados residem, minimizando drasticamente o tráfego e o overhead de rede no cluster.
- **Metadados Persistentes com Rastreabilidade Multinível:** O estado lógico de todo o cluster é mantido de forma transacional e transparente por meio de um índice mestre baseado em JSON, mapeando com precisão as coordenadas físicas, lógicas e temporais de cada arquivo.

A meta principal deste terceiro marco regulatório do projeto é validar o comportamento macro e micro do DFS sob estresse, concorrência agressiva de escrita/leitura e cenários degradados de falhas de rede ou colapso de servidores. O objetivo é certificar que o sistema consiga replicar dados de maneira íntegra, manter-se online e consistente mesmo com a queda abrupta de nós de armazenamento, processar buscas analíticas paralelas nos discos e fornecer métricas estáveis de execução.

---

## 🧠 2. Arquitetura Geral e Decomposição de Planos

O DFS foi arquitetado seguindo padrões modernos de sistemas distribuídos de larga escala (fortemente inspirado em conceitos do Google File System e Apache HDFS), organizando-se em camadas rigidamente isoladas de responsabilidade. Essa abordagem garante o desacoplamento absoluto entre as interfaces de usuário, a lógica centralizada de orquestração do cluster e os subsistemas de persistência física em disco.

### 2.1 O Plano de Controle (Control Plane) vs. O Plano de Dados (Data Plane)

O principal avanço arquitetural do Marco 3 reside na separação definitiva dos fluxos de sinalização e de tráfego pesado:

- **O Control Plane (Coordenador):** Gerencia exclusivamente metadados. Ele opera como um servidor gRPC ultraleve encarregado de escutar solicitações de mapeamento estrutural. Quando a CLI solicita um `put`, o Coordenador calcula os hashes de sharding, valida os locks de concorrência, incrementa o número da versão e devolve uma "receita de bolo" contendo os IPs e portas dos nós que devem abrigar ou fornecer os dados. O Coordenador **nunca** abre buffers para ler ou transmitir os bytes dos arquivos dos usuários.
- **O Data Plane (CLI e Workers):** Compreende a malha de tráfego de bytes brutos. De posse da tabela de roteamento fornecida pelo Control Plane, a CLI (atuando como um *Thick Client* inteligente) assume a responsabilidade de abrir streams gRPC bi-direcionais diretamente com os Nós de Armazenamento (*Storage Nodes*). Os dados trafegam ponto a ponto pela periferia da rede, escalando o throughput global de forma linear, pois a adição de novos nós expande a largura de banda de I/O de maneira diretamente proporcional, sem criar gargalos no nó central.

### 2.2 Visão Detalhada dos Componentes

Para mapear a topologia do sistema, os seguintes componentes de software cooperam dinamicamente:

- **CLI Client (Interface de Linha de Comando):** Componente que intercepta as entradas do operador humano, executa o parsing de argumentos e gerencia o ciclo de vida de um cliente persistente. Ele mantém canais HTTP/2 aquecidos (*warmed-up gRPC stubs*) para evitar a latência repetitiva de handshake TCP.
- **Coordenador Principal (Master Node):** Servidor centralizado detentor da inteligência de controle de concorrência, despacho de tarefas analíticas e centralização lógica do índice do sistema de arquivos distribuído.
- **Sharding Manager (Gerenciador de Particionamento):** Módulo matemático matemático responsável por computar e resolver funções de espalhamento hash determinísticas, garantindo a seleção rigorosa das três réplicas geográficas ($N=3$) para cada bloco de arquivo.
- **Node Registry (Registro de Nós):** Banco de dados estático e dinâmico mantido em memória pelo Coordenador que monitora as identidades, mapeamentos de sockets (IP:Porta), diretórios de trabalho físicos e status de conectividade de cada Worker do cluster.
- **Node Client Interno:** Uma instância de cliente gRPC embutida no próprio Coordenador, utilizada estritamente para comunicação inter-nós (*East-West traffic*), permitindo que o mestre envie ordens administrativas de limpeza, deleção física de blocos ou gatilhos computacionais.
- **File Service (Camada de Aplicação do Coordenador):** Orquestrador de alto nível que implementa as regras de negócio distribuídas, aplicando de forma estrita as barreiras matemáticas dos quóruns de escrita ($W=2$) e leitura ($R=2$).
- **Metadata Service (Serviço de Metadados):** Componente transacional encarregado de efetuar operações de leitura e escrita atômicas no arquivo de persistência mestre `metadata_index.json`, provendo mecanismos de isolamento para evitar corrupção de concorrência.
- **Node Service (Camada de Aplicação do Worker):** Serviço gRPC síncrono hospedado em cada Nó de Armazenamento que expõe as assinaturas de procedimentos remotos para gravação física de pedaços binários e leitura de fluxos de bytes.
- **Local Storage Manager (Gerenciador de Armazenamento Local):** Módulo que encapsula as chamadas de I/O do sistema operacional hospedeiro. Ele manipula os arquivos físicos com extensão `.chunk`, organiza subpastas baseadas em estruturas hierárquicas virtuais e garante o isolamento de caminhos.
- **MapReduce Service `[Não Feito]`:** Componente mestre do plano de controle encarregado de fracionar expressões de busca analítica, mapear a proximidade física dos blocos correspondentes e consolidar (*Reduce*) os vetores numéricos leves devolvidos pela periferia do cluster.
- **Node Compute Service `[Não Feito]`:** Motor de processamento local acoplado ao Worker. Ele varre os arquivos locais persistidos em disco, aplicando filtros algorítmicos em memória (*Map*) sem realizar qualquer tráfego de rede pesado de arquivos.

---

## 🔍 3. Diagramas de Fluxo e Arquitetura

Para documentar visualmente o comportamento operacional e o tráfego de rede síncrono estabelecido entre os componentes do DFS no Marco 3, esta seção expõe os diagramas de blocos de comunicação e as sequências de eventos cronológicos.

### 3.1 Topologia de Rede e Fluxo Geral de Comunicação

O diagrama abaixo ilustra a separação absoluta dos planos de comunicação. As linhas pontilhadas simbolizam tráfego puro de controle e sinalização de metadados, enquanto as linhas duplas contínuas simbolizam o transporte maciço de payloads binários (bytes de dados):

```text
       ===================================================================
       |                     TOPOLOGIA LOGICA DO DFS                     |
       ===================================================================

                            +-------------------+
                            |    COORDENADOR    |
                            |  (Control Plane)  |
                            +-------------------+
                              .       ^       .
                              .       |       .
      [RegisterWrite/Read]    .       |       . [Purge/Compute Ordens]
      (Sinalização/Metadados) .       |       . (gRPC Interno)
                              .       |       .
                              v       |       v
                      +-------------------------------+
                      |          CLI CLIENT           |
                      |         (Data Plane)          |
                      +-------------------------------+
                        /             |             \
                       /              |              \
     [gRPC Direct Stream]      [gRPC Direct Stream]    [gRPC Direct Stream]
     (Bytes Brutos / Payloads) (Bytes Brutos / Payload)(Bytes Brutos / Payload)
                     /                |                \
                    v                 v                 v
           +--------------+   +--------------+   +--------------+
           | STAGE NODE 1 |   | STAGE NODE 2 |   | STAGE NODE 3 |
           |   (Worker)   |   |   (Worker)   |   |   (Worker)   |
           +--------------+   +--------------+   +--------------+
           | Local Disk 1 |   | Local Disk 2 |   | Local Disk 3 |
           +--------------+   +--------------+   +--------------+
```

---

### 3.2 Diagrama de Sequência Comportamental: Operação PUT

Este diagrama detalha a ordem cronológica de chamadas de rede executadas quando o cliente injeta um novo arquivo no sistema de arquivos distribuído, destacando a validação ativa do quórum de escrita estável:

```text
CLI Client (CLI)         Coordenador (Master)       Storage Node 1      Storage Node 2      Storage Node 3
    |                             |                       |                   |                   |
    |--- 1. RegisterWrite() ----->|                       |                   |                   |
    |    (Path, Size, Chunks)     |                       |                   |                   |
    |                             |-- 2. Incrementa Ver.  |                   |                   |
    |                             |   Gera Shard Map (N=3)|                   |                   |
    |<-- 3. Retorna Shard Map ----|                       |                   |                   |
    |    (Identidade dos Nós)     |                       |                   |                   |
    |                             |                       |                   |                   |
    |--- 4. WriteChunk(chunk_0, v1) --------------------->|                   |                   |
    |--- 5. WriteChunk(chunk_0, v1) ----------------------------------------->|                   |
    |--- 6. WriteChunk(chunk_0, v1) (Paralelo) -------------------------------------------------->| (Falha/Timeout)
    |                             |                       |                   |                   |
    |<-- 7. ConfirmarEscrita(OK) -------------------------|                   |                   |
    |<-- 8. ConfirmarEscrita(OK) ---------------------------------------------|                   |
    |                             |                       |                   |                   |
    |=== 9. AVALIAÇÃO DE QUÓRUM DE ESCRITA: Recebeu 2 Confirmações Estáveis (W=2)? SIM! ===========|
    |=== 10. Operação homologada com Sucesso Absoluto no Cluster. =================================|
    |                             |                       |                   |                   |
```

---

### 3.3 Diagrama de Sequência Comportamental: Operação GET

O diagrama abaixo expõe o fluxo de recuperação descentralizada de dados, demonstrando graficamente a operação do mecanismo de anti-entropia quando a CLI se depara com uma réplica desatualizada persistida em um nó que sofreu falhas passadas:

```text
CLI Client (CLI)         Coordenador (Master)       Storage Node 1      Storage Node 2      Storage Node 3
    |                             |                       |                   |                   |
    |--- 1. GetMetadata(Logical) ->|                       |                   |                   |
    |<-- 2. Retorna Meta Mestre --|                       |                   |                   |
    |    (Espera Versão v2)       |                       |                   |                   |
    |                             |                       |                   |                   |
    |--- 3. ReadChunk(chunk_0) -------------------------->|                   |                   |
    |--- 4. ReadChunk(chunk_0) ---------------------------------------------->|                   |
    |                             |                       |                   |                   |
    |<-- 5. Retorna Bytes + [v2] -------------------------|                   |                   |
    |<-- 6. Retorna Bytes + [v1] (STALE DATA!) -------------------------------|                   |
    |                             |                       |                   |                   |
    |=== 7. ANTI-ENTROPIA: Compara v2 (Mestre) com v1 (Stale). Descarta dados do Node 2! ==========|
    |                             |                       |                   |                   |
    |--- 8. ReadChunk(chunk_0) Fallback --------------------------------------------------------->|
    |<-- 9. Retorna Bytes + [v2] -----------------------------------------------------------------|
    |                             |                       |                   |                   |
    |=== 10. QUÓRUM DE LEITURA ATINGIDO: 2 Réplicas idênticas na versão v2 obtidas (R=2). =========|
    |=== 11. Concatena os bytes e remonta o arquivo íntegro no disco do usuário. ==================|
```

---

## ⚙️ 4. Especificação de Funcionalidades e Comportamento dos Fluxos Internos

Esta seção disseca a mecânica íntima e o comportamento algorítmico de cada um dos fluxos operacionais expostos pelo ecossistema do DFS no Marco 3.

### 4.1 Operação PUT (Injeção Distribuída com Quórum e Replicação Ativa)

O comando `put` é o fluxo mais complexo do sistema, envolvendo fragmentação, alocação determinística e coordenação síncrona de concorrência. Ele opera segundo o seguinte algoritmo em tempo de execução:

1. **Interceptação e Inicialização:** O usuário invoca `python run_cli.py put <caminho_local> <caminho_logico>`. A subcamada de interface CLI intercepta o comando, valida a existência do arquivo local em disco e calcula o seu tamanho total em bytes.
2. **Cálculo de Chunks Lógicos:** O arquivo local é fatiado virtualmente. Sabendo o tamanho do arquivo e o limite de `CHUNK_SIZE` definido nas configurações centrais do sistema, a CLI calcula a quantidade exata de chunks necessários. Por exemplo, um arquivo de 10MB submetido a um `CHUNK_SIZE` de 4MB gerará 3 chunks (`chunk_0` com 4MB, `chunk_1` com 4MB e `chunk_2` com 2MB).
3. **Registro Lógico e Resolução de Roteamento:** A CLI dispara uma chamada RPC do tipo `RegisterWrite` para o Coordenador. O Coordenador intercepta a chamada e abre uma transação atômica em seu índice de metadados. Se o arquivo já existir, o Coordenador incrementa de forma incremental o número sequencial da versão global do arquivo (`v1` para `v2`, etc.). Se for um arquivo inédito, inicia na versão `v1`.
4. **Resolução de Sharding Geográfico:** Para cada um dos chunks calculados, o Coordenador invoca o motor de sharding determinístico. Este motor aplica uma função hash sobre a composição da string do caminho lógico do arquivo fundida ao ID sequencial do bloco (`hash(logical_path + chunk_id)`). O resultado matemático aponta de forma precisa quais serão as três instâncias de Nós de Armazenamento ($N=3$) que atuarão como guardiãs daquelas réplicas específicas. O Coordenador grava essa topologia preliminar no arquivo JSON de índice e devolve o mapa de distribuição consolidado para a CLI.
5. **Streaming Direto de Payloads Binários:** A CLI recebe a tabela de roteamento contendo os IPs e portas dos nós associados a cada chunk. A CLI abre conexões gRPC diretas com cada um dos nós mapeados. Utilizando chamadas de streaming binário, a CLI transmite concorrentemente e em paralelo os blocos de bytes diretamente para os discos dos Workers, informando juntamente a tag da versão global gerada.
6. **Consolidação Física nos Workers:** Ao interceptar a chamada gRPC `WriteChunk`, o Worker aciona o seu gerenciador de armazenamento local. O bloco de bytes bruto é gravado no disco rígido local do nó, recebendo uma nomenclatura padronizada e imutável que anexa o identificador do chunk e a tag da versão legitimada (`/data/nodes/nodeX/pasta_logica/arquivo.txt.chunk_0.ver_1`).
7. **Avaliação Matemática do Quórum de Escrita ($W=2$):** A CLI atua como árbitra do quórum de gravação. A operação de escrita de um chunk específico só é declarada consolidada se, e somente se, a CLI receber mensagens de confirmação de sucesso vindas de, no mínimo, **2 nós de armazenamento distintos ($W=2$)**. Caso um dos três nós designados esteja offline ou sofra um timeout de rede, a CLI ignora temporariamente a falha daquele nó individual, pois o quórum mínimo de 2 respostas foi atingido, garantindo a estabilidade e finalizando o fluxo com sucesso. Se 2 ou mais nós falharem simultaneamente, a CLI aborta o processo e reporta falha crítica catastrófica de quórum.

### 4.2 Operação GET (Recuperação Descentralizada e Resolução de Anti-Entropia)

O fluxo de recuperação de dados implementa as garantias de consistência forte e prevenção contra leituras obsoletas. O fluxo se desdobra nas seguintes etapas:

1. **Requisição de Metadados Mestre:** O usuário executa `python run_cli.py get <caminho_logico> <destino_local>`. A CLI conecta-se via gRPC ao Coordenador invocando o método `GetMetadata`. O Coordenador efetua uma busca síncrona no índice `metadata_index.json`, captura a entrada lógica do arquivo e extrai dois metadados vitais: o mapa físico de alocação de chunks e o número exato da última versão estável consolidada globalmente no sistema de arquivos. Essas informações são envelopadas em uma mensagem Protobuf e enviadas de volta à CLI.
2. **Varredura e Disparo de Canais de I/O de Leitura:** De posse do mapa mestre, a CLI inicia uma iteração sequencial para remontar o arquivo bloco por bloco. Para o `chunk_0`, a CLI identifica os três nós que teoricamente guardam suas réplicas. De forma concorrente, a CLI dispara chamadas remotas do tipo `ReadChunk` para **pelo menos 2 nós de armazenamento pertencentes àquela lista ($R=2$)**.
3. **Mecanismo Ativo de Anti-Entropia (*Stale Read Prevention*):** Ao receber os fluxos de bytes acompanhados das tags de versão carimbadas pelos nós consultados, a CLI executa uma validação lógica estrita. Se o Coordenador informou que a versão mestre atual do arquivo é a `v3`, e o `Node_1` retornar o bloco carimbado com `v3`, mas o `Node_2` (por ter ficado temporariamente offline durante um PUT anterior) retornar o bloco carimbado com `v2`, a CLI detecta a assincronia e a obsolescência imediatamente. O bloco de bytes defasado do `Node_2` é sumariamente descartado. A CLI ativa um mecanismo de fallback em tempo de execução e conecta-se ao terceiro nó da lista (`Node_3`). Se o `Node_3` retornar a versão legítima `v3`, o quórum de leitura de 2 respostas consistentes ($R=2$) é satisfeito.
4. **Remontagem e Escrita Linear em Disco:** Após extrair e convalidar com segurança os bytes puros pertencentes à versão legítima mais recente de todos os chunks do arquivo, a CLI concatena sequencialmente os fluxos recebidos e grava o fluxo binário unificado no disco local do usuário, replicando perfeitamente o arquivo original sem nenhuma corrupção ou perda de integridade.

### 4.3 Operação RM (Remoção Distribuída e Rotina de Expurgamento Físico)

A deleção de arquivos no DFS opera de forma a garantir que nenhum dado órfão ou bloco fantasma permaneça consumindo os discos rígidos do cluster. O fluxo comporta-se da seguinte forma:

1. **Invalidação Lógica Instantânea:** O usuário aciona `python run_cli.py rm <caminho_logico>`. A CLI emite uma chamada `DeleteRequest` direcionada ao gRPC do Coordenador. O Coordenador remove imediatamente a chave correspondente ao arquivo de dentro do índice mestre em memória e atualiza o arquivo JSON em disco. A partir deste exato milésimo de segundo, o arquivo deixa de existir para qualquer operação de listagem ou leitura, garantindo atomicidade lógica.
2. **Disparo de Sinais de Purga Assíncronos:** O Coordenador ativa seu componente `Node Client Interno`. Ele itera sobre a tabela de registros de nós ativos e, em paralelo, despacha chamadas remotas de controle denominadas `PurgeFile` para todos os nós de armazenamento cadastrados no ecossistema.
3. **Varredura e Limpeza Física nos Nós:** Cada Worker intercepta a ordem de purga contendo a assinatura do caminho virtual deletado. O gerenciador de armazenamento local do nó localiza todos os arquivos físicos em seu disco que contenham correspondência estrutural com o arquivo excluído, apagando todos os chunks e todas as versões históricas remanescentes associadas àquele caminho.
4. **Rotina de Limpeza de Subpastas Vazias:** Após deletar os arquivos `.chunk`, o módulo `Local Storage` do Worker executa uma varredura recursiva de baixo para cima nas pastas físicas locais. Se a exclusão do arquivo deixou diretórios ou subpastas virtuais completamente vazias dentro do nó, essas pastas são destruídas pelo sistema operacional para otimizar a árvore de diretórios do storage host.

### 4.4 Operação LIST (Auditoria e Inspeção do Índice de Metadados)

O comando `list` provê uma janela de auditoria e transparência absoluta sobre o estado lógico atual da infraestrutura distribuída:

1. **Invocação:** O usuário digita `python run_cli.py list`. A CLI aciona o stub gRPC correspondente no Coordenador.
2. **Leitura e Consolidação:** O Coordenador intercepta a chamada e faz um dump síncrono do `metadata_index.json`. O sistema processa os metadados de forma a consolidar as informações para consumo humano.
3. **Formatação de Saída:** O Coordenador devolve uma coleção estruturada de mensagens Protobuf contendo o caminho lógico completo de cada arquivo, seu tamanho total consolidado em bytes, a quantidade exata de chunks em que foi fragmentado pelo sistema e, criticamente, o número da versão global atual daquela entrada. A CLI recebe os dados e renderiza uma tabela formatada no terminal do operador.

### 4.5 Operação WORDCOUNT (MapReduce Paralelo Orientado a Localidade) `[Não Feito]`

Projetado para computação distribuída analítica sobre o ecossistema do DFS, o fluxo opera minimizando drasticamente a movimentação de dados na rede:

1. **Disparo Analítico:** O usuário executa `python run_cli.py wordcount <caminho_logico> <termo_busca>`.
2. **Mapeamento de Afinidade por Localidade (*Data Locality*):** O Coordenador recebe a requisição. Em vez de ler o arquivo, ele consulta o mapa de chunks do arquivo. Ele identifica, por exemplo, que o `chunk_0` está no `Node_1`, o `chunk_1` está no `Node_2`, e assim por diante.
3. **Despacho Concorrente de Tarefas MapRPC:** O Coordenador aciona o `MapReduce Service` no Control Plane. Ele envia uma chamada gRPC leve (`RunMapTask`) diretamente para o `Node Compute Service` dos nós detentores físicos dos dados, passando o termo de busca.
4. **Processamento Local em Disco (Fase Map):** Cada Worker recebe a ordem de computação. Ele abre os chunks locais persistidos em seu próprio disco rígido, lê os bytes diretamente para a memória local do servidor e executa uma contagem de strings concorrente de alta velocidade. O nó calcula o número de ocorrências localmente e devolve para o Coordenador apenas um número inteiro leve (ex: "Node 1 encontrou 45 ocorrências"). O arquivo de dados nunca viaja pela rede.
5. **Consolidação Mestre (Fase Reduce):** O Coordenador coleta as respostas numéricas leves vindas da periferia do cluster, executa a função de agregação matemática (soma vetorial de todas as parciais) e devolve o total final consolidado instantaneamente para a CLI do usuário.

---

## 🧩 5. Aprofundamento dos Conceitos Distribuídos e Engenharia de Rede

A robustez do DFS no Marco 3 repousa sobre a aplicação rigorosa de conceitos matemáticos e de engenharia de redes de computadores de sistemas distribuídos modernos.

### 5.1 Fator de Replicação ($N=3$)

O Fator de Replicação estipula o nível de redundância física do cluster. Ao definir $N=3$, o sistema estabelece que para cada unidade de informação atômica injetada no ecossistema, devem coexistir três cópias exatas em domínios de falha isolados. Em ambientes de produção de larga escala, essa configuração garante que mesmo diante do colapso completo de um rack de servidores, os dados permaneçam acessíveis. No escopo do projeto, garante que até mesmo a perda catastrófica de nós inteiros de armazenamento não resulte em perda de persistência dos arquivos dos usuários.

### 5.2 Consistência Forte via Modelo de Quórum Estrito ($W=2, R=2$)

Para governar a consistência de dados sem depender de protocolos pesados e centralizados de travamento bidirecional (como Two-Phase Locking), o DFS implementa o Modelo de Quórum Descentralizado. O alicerce desse modelo baseia-se na inequação fundamental de sistemas distribuídos:

$$W + R > N$$

Onde:
- $N$ representa o Fator de Replicação do sistema ($N=3$).
- $W$ representa o Quórum Mínimo de Escrita Estável ($W=2$).
- $R$ representa o Quórum Mínimo de Leitura Consistente ($R=2$).

Substituindo os valores parametrizados no sistema na equação, obtemos:

$$2 + 2 > 3 \implies 4 > 3$$

Como a soma dos nós consultados na escrita com os nós consultados na leitura é estritamente maior do que o número total de réplicas existentes no cluster, o Princípio da Casa dos Pombos garante matematicamente que o conjunto de nós interceptados durante uma operação de leitura **obrigatoriamente fará interseção com pelo menos um nó** que participou da operação de escrita estável mais recente. 



Essa interseção assegura consistência forte (linearidade), pois mesmo que o cluster possua nós desatualizados, a CLI fatalmente consultará pelo menos um nó detentor da versão correta, utilizando o mecanismo de anti-entropia para descartar os dados velhos e propagar a resposta legítima.

### 5.3 Sharding Determinístico Baseado em Hashing por Chunk

Para eliminar a necessidade de manter tabelas de roteamento pesadas, dinâmicas e centralizadas para cada chunk do sistema (o que degradaria a memória do Coordenador a longo prazo), o DFS adota sharding baseado em algoritmos de espalhamento hash. A chave de hash baseia-se na junção estrita do caminho lógico do arquivo no DFS com o índice sequencial do seu chunk:

$$\text{Chave} = \text{hash}(\text{logical\_path} + \text{str}(\text{chunk\_id}))$$

Ao aplicar a operação matemática de módulo sobre a quantidade total de nós de armazenamento ativos registrados no cluster, o sistema obtém o índice do nó primário responsável por hospedar aquela réplica específica. As réplicas secundárias e terciárias são alocadas nos nós subsequentes da cadeia circular de armazenamento de forma determinística. Isso assegura:
- Distribuição homogênea e balanceada da carga de armazenamento entre os discos dos nós.
- Capacidade de qualquer componente calcular instantaneamente a localização teórica de um bloco sem necessidade de realizar consultas complexas a bancos de dados relacionais.

### 5.4 Versionamento Atômico de Arquivos

O controle de versionamento opera como o token imutável de validação definitiva para os algoritmos de consistência de quórum. Quando ocorre uma mutação (`put`), o incremento da versão ocorre de maneira atômica no plano de controle. Ao persistir os dados, a string de versão é gravada em disco acoplada ao nome do arquivo físico. Se um nó de armazenamento sofrer uma queda, ficar indisponível por horas e retornar ao cluster, seus arquivos físicos ainda estarão carimbados com a versão antiga. Durante um quórum de leitura, esse carimbo antigo funcionará como uma flag de invalidação, permitindo que a CLI identifique e isole o nó defasado imediatamente.

### 5.5 Abandono de Sockets TCP Legados e Tuning de Canais gRPC/HTTP2

O gRPC opera nativamente sobre conexões HTTP/2, trazendo vantagens brutas de performance como multiplexação de streams (várias requisições trafegando simultaneamente por uma única conexão TCP), compressão de cabeçalhos binários e mecanismos eficientes de keep-alive a nível de aplicação. 

No entanto, por padrão de fábrica, o framework gRPC limita o tamanho de transmissão de mensagens individuais a 4MB como salvaguarda de estouro de memória. Como um DFS lida nativamente com grandes volumes de dados binários, essa limitação paralisaria o tráfego de chunks densos. Para sanar essa restrição de forma elegante, a arquitetura do Marco 3 introduz uma camada de sintonia fina (*tuning*) através da injeção do dicionário de inicialização `GRPC_OPTIONS`. Configura-se explicitamente os parâmetros de controle de tamanho máximo de envio e recebimento de mensagens para **64MB**:

- `grpc.max_send_message_length`: Configurado para `64 * 1024 * 1024` bytes.
- `grpc.max_receive_message_length`: Configurado para `64 * 1024 * 1024` bytes.

Essa modificação arquitetural viabilizou o tráfego fluido de dados em alta velocidade, blindando o ecossistema distribuído contra exceções críticas de exaustão de recursos (`RESOURCE_EXHAUSTED`).

---

## 🗂️ 6. Mapeamento e Análise Granular da Estrutura do Projeto

Abaixo encontra-se a árvore de diretórios oficial do ecossistema do DFS no Marco 3, detalhando minuciosamente a função lógica de cada componente:

```text
MARCO3/
├── .venv/                               # Ambiente virtual isolado Python 3 contendo interpretador e libs.
├── DFS_M3/                              # Diretório mestre que encapsula o código-fonte do pacote DFS.
│   ├── pyproject.toml                   # Arquivo de especificação de metadados, build-system e empacotamento moderno.
│   ├── requirements.txt                 # Listagem de dependências de produção (grpcio e grpcio-tools).
│   │
│   ├── data/                            # Subpastas destinadas à simulação de persistência em disco rígido.
│   │   ├── metadata/                    # Diretório restrito de armazenamento físico do Control Plane (Coordenador).
│   │   │   └── metadata_index.json      # O banco de dados JSON centralizador de todo o índice lógico do DFS.
│   │   └── nodes/                       # Diretórios simulando discos físicos independentes dos servidores Workers.
│   │       ├── node1/                   # Partição de armazenamento físico isolada pertencente ao Nó 1.
│   │       ├── node2/                   # Partição de armazenamento físico isolada pertencente ao Nó 2.
│   │       └── node3/                   # Partição de armazenamento físico isolada pertencente ao Nó 3.
│   │
│   ├── dfs/                             # Módulo Python principal que centraliza o core logicial do sistema.
│   │   ├── __init__.py                  # Arquivo padrão para sinalizar ao interpretador que a pasta é um pacote importável.
│   │   ├── config.py                    # Ficheiro de constantes globais (portas sockets, CHUNK_SIZE, GRPC_OPTIONS).
│   │   ├── client.py                    # Implementação da classe cliente gRPC persistente utilizada pela interface da CLI.
│   │   │
│   │   ├── application/                 # Camada de serviços lógicos de regras de negócio de alto nível.
│   │   │   ├── file_service.py          # Implementação gRPC do Coordenador: Roteamento, Quórum e Versionamento.
│   │   │   ├── metadata_service.py      # Operações transacionais de leitura/escrita no JSON de metadados mestre.
│   │   │   ├── node_service.py          # Servicer gRPC acoplado aos nós: Gerencia chamadas WriteChunk e ReadChunk.
│   │   │   ├── mapreduce_service.py     # [Não Feito] Master Engine: Orquestração analítica baseada em localidade física.
│   │   │   └── node_compute_service.py  # [Não Feito] Worker Engine: Varredura local concorrente em disco para tarefas Map.
│   │   │
│   │   ├── cluster/                     # Camada responsável pelo gerenciamento de topologia do cluster distribuído.
│   │   │   ├── node_client.py           # Abstração de canais de comunicação internos Coordenador -> Nós (East-West).
│   │   │   ├── node_registry.py         # Mapeamento e cadastro dinâmico de endereços sockets, IDs e vitalidade dos nós.
│   │   │   └── sharding.py              # Algoritmo matemático de espalhamento determinístico por hash de chunks.
│   │   │
│   │   ├── interface/                   # Camada exposta para inicialização de processos e interação com o usuário.
│   │   │   ├── cli.py                   # Parsing de argumentos de comandos e loop interativo estável da CLI.
│   │   │   ├── server.py                # Inicializador do servidor gRPC mestre do Coordenador (Porta 50051).
│   │   │   └── storage_node.py          # Lançador unificado do servidor gRPC dos Nós de Armazenamento (Portas 50052+).
│   │   │
│   │   ├── storage/                     # Camada de interação de baixo nível com o hardware hospedeiro.
│   │   │   └── local_storage.py         # Abstração física de I/O de bytes em formato binário e limpeza de subpastas vazias.
│   │   │
│   │   └── pb/                          # Diretório contendo os artefatos compilados pelo compilador de Protocol Buffers.
│   │       ├── __init__.py              # Arquivo de inicialização de pacote para as classes compiladas de rede.
│   │       ├── dfs_pb2.py               # Classes de dados de mensagens binárias geradas automaticamente pelo Protobuf.
│   │       └── dfs_pb2_grpc.py          # Interfaces stubs de clientes e servicers geradas pelo framework gRPC.
│   │
│   ├── proto/                           # Diretório detentor das especificações agnósticas de contratos de interface.
│   │   └── dfs.proto                    # O arquivo de especificação IDL original que dita as regras de mensagens do cluster.
│   │
│   ├── scripts/                         # Automações secundárias e rotinas auxiliares do sistema de arquivos.
│   │   └── run_benchmarks.py            # [Não Feito] Script de testes para avaliação de throughput por variação de chunk.
│   │
│   └── docs/                            # Documentação técnica profunda e relatórios de métricas do sistema.
│       ├── decisoes_marco3.md           # [Não Feito] Memorial descritivo com defesa arquitetural detalhada perante o CAP.
│       └── benchmark_report.md          # [Não Feito] Relatório analítico consolidado com gráficos de carga e latência de rede.
│
├── README.md                            # Este exaustivo manual de engenharia e operações técnicas.
├── run_cluster.py                       # Orquestrador mestre assíncrono para subir as 4 instâncias gRPC locais.
└── run_cli.py                           # Ponto de entrada simplificado global para execução de comandos do Data Plane.
```

---

## 🧭 7. O Que Faz Cada Arquivo: Análise Granular Detalhada

Esta seção provê uma autópsia técnica detalhada sobre a responsabilidade funcional interna de cada arquivo que compõe o ecossistema de software do DFS no Marco 3.

### 7.1 Arquivos do Diretório Raiz `MARCO3/`

- **`run_cluster.py`:** Atua como o maestro de processos da infraestrutura local. Utilizando o módulo assíncrono `subprocess` do Python, ele inicializa de forma concorrente e isolada quatro processos de sistema operacional independentes: uma instância do Coordenador gRPC (porta 50051) e três instâncias distintas de Nós de Armazenamento (portas 50052, 50053 e 50054). O script intercepta as saídas padrão (`stdout` e `stderr`) de todos esses subprocessos e as canaliza de forma multiplexada para um terminal de console unificado, formatando os logs com cores e prefixos identificadores, facilitando a depuração visual do comportamento interno do cluster em tempo real.
- **`run_cli.py`:** Funciona como o portal de teletransporte para o usuário final. Ele captura os argumentos repassados na linha de comando na raiz do projeto, ajusta as variáveis de caminhos lógicos do sistema no dicionário `sys.path` e delega a execução diretamente para o interpretador de comandos interno do pacote `dfs`, permitindo a invocação limpa dos fluxos operacionais sem exigir navegação interna de pastas por parte do usuário.

### 7.2 Arquivos do Core Package `DFS_M3/dfs/`

- **`config.py`:** Centraliza as variáveis de configuração que ditam o comportamento de todo o cluster distribuído. Define mapeamentos estáticos de endereços IP locais (`127.0.0.1`), portas sockets reservadas para cada participante do sistema, o valor numérico rígido do fator de replicação ($N=3$), a parametrização do tamanho limite do bloco lógico de corte (`CHUNK_SIZE`), além de instanciar a estrutura mutável `GRPC_OPTIONS`. Esta estrutura configura explicitamente as janelas máximas de tráfego HTTP/2 de entrada e saída para 64MB, blindando o sistema contra estouros de buffer e exceções prematuras de exaustão de capacidade física de transporte.
- **`client.py`:** Implementa a inteligência do cliente gRPC utilizado pela interface CLI. É encarregado de encapsular a instanciação física dos stubs gerados pelo compilador e gerenciar o ciclo de vida dos canais de comunicação. Possui a lógica complexa de fatiar arquivos locais em fluxos de bytes puros e realizar o streaming direto ponto a ponto com as APIs gRPC dos Workers de armazenamento, executando fallbacks de rede automáticos e validações matemáticas de quórum na leitura e na escrita.

### 7.3 Camada `application/` (Lógica de Serviços)

- **`file_service.py`:** Aloja a implementação concreta da classe abstrata Servicer gerada a partir do contrato Protobuf para o plano de controle. Ele traduz os métodos remotos RPC expostos pelo Coordenador (como `RegisterWrite`, `GetMetadata`, `DeleteRequest`). Contém o cerne algorítmico que manipula atomicamente os números de versão dos arquivos, assegura a consistência lógica mestre do cluster e gerencia os locks lógicos necessários para impedir condições de corrida durante operações de escrita simultâneas no ecossistema.
- **`metadata_service.py`:** É o motor transacional de persistência do Coordenador. Ele gerencia o arquivo `metadata_index.json` localizado em `data/metadata/`. Provê métodos síncronos e protegidos por semáforos para leitura ultrarrápida do índice de arquivos em memória, atualização de estruturas de mapas de shards e gravação atômica em disco. Também incorpora algoritmos para mapeamento de afinidade espacial, calculando quais nós possuem maior densidade de dados locais para subsidiar decisões computacionais do MapReduce.
- **`node_service.py`:** Implementa a classe Servicer gRPC executada nos servidores de armazenamento (Workers). É responsável por expor as interfaces operacionais de I/O físico manipuladas diretamente pelo cliente. Ele traduz as chamadas de streaming remoto `WriteChunk` (recebendo bytes contínuos e vertendo-os para o gerenciador de disco local) e `ReadChunk` (abrindo buffers locais, extraindo bytes e carimbando-os com tags de versão antes de jogá-los no canal HTTP/2).
- **`mapreduce_service.py` `[Não Feito]`:** Componente de alto nível acoplado ao Coordenador que atua como o Master do motor de processamento distribuído. Sua função é fracionar uma consulta analítica textual, interrogar o `metadata_service` para mapear a localidade física de cada bloco pertencente ao arquivo alvo, instanciar stubs gRPC de computação interna e disparar chamadas assíncronas concorrentes de processamento para os Workers, aguardando os retornos numéricos leves para computar a agregação matemática final (*Reduce*).
- **`node_compute_service.py` `[Não Feito]`:** Serviço operário acoplado ao servidor de armazenamento que intercepta os sinais computacionais enviados pelo Master do MapReduce. Ele implementa threads de varredura que abrem localmente os arquivos de extensão `.chunk` no disco rígido local do nó, aplicam expressões regulares ou filtros lineares de string em alta velocidade direto na memória ram da máquina hospedada e devolvem resultados numéricos consolidados de frequências de termos, sem gerar tráfego de rede volumoso.

### 7.4 Camada `cluster/` (Gerenciamento de Topologia)

- **`node_registry.py`:** Gerencia a tabela estática e dinâmica de participantes do cluster. Mantém o mapeamento rigoroso que correlaciona o ID exclusivo de um nó (ex: `Node_1`) com a sua respectiva porta socket gRPC ativa e o seu diretório físico de isolamento de dados no sistema operacional, servindo como o dicionário definitivo de consulta de presença do cluster.
- **`sharding.py`:** Contém a função matemática pura de distribuição hashing do ecossistema. Ela recebe a string do caminho lógico de um arquivo fundida ao ID numérico do chunk, calcula o hash criptográfico ou numérico correspondente e aplica a operação aritmética de módulo sobre o total de nós funcionais do sistema. Esse cálculo retorna de forma determinística os três nós ordenados encarregados de abrigar as réplicas do respectivo bloco, garantindo espalhamento homogêneo e balanceamento de carga de disco nativo no ecossistema.
- **`node_client.py`:** Abstração de cliente gRPC embutida para comunicações internas privadas do cluster (*Control Plane to Workers*). É utilizada pelo Coordenador para estabelecer canais remotos privados com os nós de armazenamento, permitindo o despacho de ordens administrativas assíncronas de expurgamento físico de arquivos (`PurgeFile`) ou sinalizações de controle de processamento computacional.

### 7.5 Camada `storage/` e Artefatos Compilados `pb/` / `proto/`

- **`local_storage.py`:** A camada de mais baixo nível do sistema, que interage diretamente com as APIs de I/O de arquivos do sistema operacional hospedeiro. Executa operações atômicas de criação de arquivos binários de escrita em blocos (`wb`) e leitura binária sequencial (`rb`) sob isolamento estrito de caminhos. Incorpora algoritmos de remoção recursiva que vasculham a árvore física de diretórios locais do nó e destróem pastas vazias geradas após operações massivas de remoção de dados (`rm`).
- **`dfs_pb2.py`:** Código Python gerado automaticamente pelo compilador `protoc` do Protocol Buffers a partir das especificações do arquivo `dfs.proto`. Contém as classes de dados de mensagens estruturadas binárias altamente otimizadas, responsáveis por realizar a serialização e desserialização rápida de dados em tempo de execução. **Não deve ser editado manualmente.**
- **`dfs_pb2_grpc.py`:** Código de infraestrutura de rede gerado automaticamente pelo compilador gRPC. Fornece os stubs de comunicação cliente e as classes abstratas base (*Servicers*) necessárias para a montagem e inicialização dos servidores HTTP/2 gRPC do Coordenador e dos Nós de Armazenamento. **Não deve ser editado manualmente.**
- **`dfs.proto`:** O contrato definitivo IDL (Interface Definition Language) do ecossistema. Especifica de forma estrita e agnóstica de linguagem todas as estruturas de mensagens trocadas no cluster (pedidos de escrita, respostas de metadados, streams binários de chunks) e declara as assinaturas de procedimentos de serviços que governam a interação de todo o cluster distribuído do DFS.

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
Sempre que o arquivo de especificação de interfaces de rede `DFS_M3/dfs/pb/dfs.proto` sofrer qualquer tipo de modificação de atributos ou adição de assinaturas RPC, mude temporariamente o escopo de diretório e invoque a ferramenta do compilador nativo para atualizar o pacote interno de stubs `pb/`:
```bash
cd DFS_M3
python -m grpc_tools.protoc -I=dfs/pb --python_out=./dfs/pb --grpc_python_out=./dfs/pb dfs/pb/dfs.proto
cd ..
```

### Step 5: Inicializar os Diretórios Físicos do Simulador de Discos
Garanta a existência prévia da árvore de diretórios necessária para simular o isolamento físico dos storages locais dos nós e da pasta de metadados mestre do Coordenador:
```bash
mkdir -p DFS_M3/data/metadata
mkdir -p DFS_M3/data/nodes/node1
mkdir -p DFS_M3/data/nodes/node2
mkdir -p DFS_M3/data/nodes/node3
```

### Step 6: Lançar e Subir o Cluster gRPC Completo Online
Para colocar toda a infraestrutura distribuída online em uma única chamada de console, invoque o script centralizador de subprocessos assíncronos:
```bash
python run_cluster.py
```
*Atenção extrema: Este terminal passará a cuspir logs concorrentes unificados gerados simultaneamente pelo Coordenador e pelos 3 nós de armazenamento ativos. Mantenha esta janela aberta e intocada durante toda a sua simulação de testes.*

### Step 7: Interagir com o DFS via Interface CLI
Abra uma janela de terminal completamente independente, garanta a ativação prévia da sua `venv` corporativa e execute comandos operacionais utilizando o lançador unificado do Data Plane:
```bash
python run_cli.py <comando> [argumentos]
```

---

## 🧪 9. Exemplos de Uso Prático e Simulações de Cenários

### 9.1 Preparar um Arquivo Local para Testes de Transmissão
Gere um arquivo textual contendo dados arbitrários na raiz do projeto para servir de cobaia de I/O distribuído:
```bash
echo "Sistemas distribuidos e replicados utilizando quorum estrito gRPC Marco 3" > DFS_M3/teste.txt
```

### 9.2 Injetar o Arquivo Local no Ecossistema DFS (PUT)
Envie o arquivo local para uma rota virtual parametrizada dentro da árvore lógica do sistema de arquivos distribuído:
```bash
python run_cli.py put DFS_M3/teste.txt /documentos/financeiro/dados.txt
```

### 9.3 Auditar o Índice e Metadados Globais do Cluster (LIST)
Consulte o estado de registro lógico atualizado para checar a existência, tamanho consolidado e versão do arquivo injetado:
```bash
python run_cli.py list
```

### 9.4 Recuperar o Arquivo Distribuído via Validação de Quórum (GET)
Efetue a descarga descentralizada dos blocos diretamente dos nós de armazenamento, remontando o arquivo de forma limpa em disco:
```bash
python run_cli.py get /documentos/financeiro/dados.txt copia_recuperada.txt
```

### 9.5 Disparar Processamento Analítico Local por Localidade `[Não Feito]` (WORDCOUNT)
Acione a rotina computacional do MapReduce para executar busca e contagem paralela de strings diretamente nos discos dos Workers:
```bash
python run_cli.py wordcount /documentos/financeiro/dados.txt "distribuidos"
```

### 9.6 Expurgar Arquivo Físico e Limpar Discos dos Nós (RM)
Remova logicamente o arquivo do índice e dispare ordens de destruição física de chunks em todas as partições do cluster:
```bash
python run_cli.py rm /documentos/financeiro/dados.txt
```

### 9.7 Entrar no Modo Interativo Persistente de Alta Velocidade da CLI
Invoque a interface sem passar argumentos adicionais para iniciar o loop de sessão interativa do DFS:
```bash
python run_cli.py
```
*Vantagem arquitetural crucial: Este modo mantém os canais gRPC instanciados em cache na sessão ativa do terminal do usuário, eliminando completamente o overhead temporal de reabertura e fechamento de conexões e handshakes HTTP/2 a cada comando sequencial digitado.*

---

## 🔍 10. Detalhamento Técnico Profundo dos Fluxos de Operação (Traces Lógicos)

Esta seção documenta o encadeamento detalhado de eventos lógicos estruturados que guiam a execução interna do sistema operacional do DFS em cada cenário operacional do ecossistema.

### 10.1 Rastreamento Completo do Fluxo PUT (Escrita Distribuída)

```text
[Operador CLI] ---> Executa comando put teste.txt /docs/documento.txt
  |
  +---> CLI avalia tamanho local (ex: 8MB) -> Calcula necessidade de 2 Chunks de 4MB (chunk_0, chunk_1)
  |
  +---> CLI emite gRPC [RegisterWriteRequest] ---> [Coordenador (Porta 50051)]
          |
          +---> Coordenador intercepta requisição e abre Lock de Concorrência para '/docs/documento.txt'
          +---> Busca existência prévia: Não encontrado. Instancia metadados iniciais.
          +---> Incrementa atómicamente a versão lógica mestre do arquivo para -> Version [v1]
          +---> Ativa Motor Sharding por Hashing Determinístico:
          |       - hash('/docs/documento.txt' + 'chunk_0') % 3 -> Determina Réplicas: Node1, Node2, Node3
          |       - hash('/docs/documento.txt' + 'chunk_1') % 3 -> Determina Réplicas: Node2, Node3, Node1
          +---> Grava estrutura de alocação física no arquivo mestre 'metadata_index.json'
          +---> Libera Lock de Concorrência
          |
[Coordenador] ---> Devolve gRPC [RegisterWriteResponse] contendo Mapa de Roteamento de Chunks ---> [CLI]
  |
  +---> CLI interpreta o Shard Map recebido e inicia loops de streaming paralelos assíncronos:
  |
  +===> TRANSMISSÃO DO CHUNK 0 (Foco nas Réplicas: Node1, Node2, Node3):
  |       |--- CLI abre gRPC Stream [WriteChunk] direto para Node1 (Porta 50052) -> Transmite bytes + v1. Node1 grava e confirma OK.
  |       |--- CLI abre gRPC Stream [WriteChunk] direto para Node2 (Porta 50053) -> Transmite bytes + v1. Node2 grava e confirma OK.
  |       |--- CLI abre gRPC Stream [WriteChunk] direto para Node3 (Porta 50054) -> Transmite bytes + v1. Node3 falha (TIMEOUT!).
  |       +---> CLI avalia Quórum do Chunk 0: Recebeu 2 confirmações estáveis de escrita. Quórum W=2 ATINDIGO com sucesso!
  |
  +===> TRANSMISSÃO DO CHUNK 1 (Foco nas Réplicas: Node2, Node3, Node1):
  |       |--- CLI abre gRPC Stream [WriteChunk] direto para Node2 (Porta 50053) -> Transmite bytes + v1. Node2 grava e confirma OK.
  |       |--- CLI abre gRPC Stream [WriteChunk] direto para Node3 (Porta 50054) -> Transmite bytes + v1. Node3 falha (TIMEOUT!).
  |       |--- CLI abre gRPC Stream [WriteChunk] direto para Node1 (Porta 50052) -> Transmite bytes + v1. Node1 grava e confirma OK.
  |       +---> CLI avalia Quórum do Chunk 1: Recebeu 2 confirmações estáveis de escrita. Quórum W=2 ATINGIDO com sucesso!
  |
[CLI] ---> Imprime mensagem de sucesso em tela e homologa a escrita estável do arquivo no cluster distribuído.
```

---

### 10.2 Rastreamento Completo do Fluxo GET (Leitura com Resolução de Conflitos e Anti-Entropia)

```text
[Operador CLI] ---> Executa comando get /docs/documento.txt copia.txt
  |
  +---> CLI emite chamado gRPC [GetMetadataRequest] ---> [Coordenador (Porta 50051)]
          |
          +---> Coordenador executa busca síncrona no índice JSON em disco
          +---> Localiza arquivo. Captura a versão mestre registrada: Version [v2]
          +---> Coordenador extrai o mapa físico de nós de réplicas associados aos chunks
          |
[Coordenador] ---> Devolve gRPC [GetMetadataResponse] contendo Versão Mestre v2 e Mapa de Chunks ---> [CLI]
  |
  +---> CLI inicia loop sequencial de reconstrução do arquivo a partir dos dados periféricos:
  |
  +===> COLETA E RESOLUÇÃO DO CHUNK 0:
          |--- CLI dispara chamado gRPC [ReadChunk] direto para Node1 (Porta 50052)
          |--- CLI dispara chamado gRPC [ReadChunk] direto para Node2 (Porta 50053)
          |
          |<--- Node1 responde com sucesso, entregando payload binário carimbado com Versão [v2]
          |<--- Node2 responde com sucesso, entregando payload binário carimbado com Versão [v1] (STALE DATA!)
          |
          +===> MECANISMO DE ANTI-ENTROPIA DA CLI EM AÇÃO:
          |      - Compara versão do Node1 (v2) com Versão Mestre Esperada (v2) -> LEGÍTIMO.
          |      - Compara versão do Node2 (v1) com Versão Mestre Esperada (v2) -> DEFASADO/STALE!
          |      - CLI descarta sumariamente os bytes oriundos do Node2 para evitar leituras desatualizadas.
          |      - Quórum de leitura do Chunk 0 no momento possui apenas 1 resposta válida. Necessita R=2.
          |
          |--- CLI ativa Fallback em runtime e dispara gRPC [ReadChunk] de resgate para o Node3 (Porta 50054)
          |<--- Node3 responde com sucesso, entregando payload binário carimbado com Versão [v2]
          |
          +===> CLI reavalia Quórum do Chunk 0: Possui agora 2 blocos validados na versão legítima v2. Quórum R=2 ATINGIDO!
  |
  +---> CLI concatena os bytes purificados do Chunk 0 e Chunk 1 livres de anomalias temporais.
  |
[CLI] ---> Grava o fluxo binário consolidado em 'copia.txt' no disco rígido do usuário final de forma transparente.
```

---

### 10.3 Rastreamento Completo do Fluxo RM (Exclusão Síncrona e Descentralizada)

```text
[Operador CLI] ---> Executa comando rm /docs/documento.txt
  |
  +---> CLI emite chamada gRPC [DeleteRequest] ---> [Coordenador (Porta 50051)]
          |
          +---> Coordenador intercepta chamada e captura Lock de Escrita
          +---> Remove de forma imediata e definitiva a chave '/docs/documento.txt' do dicionário de metadados
          +---> Força gravação síncrona de atualização atômica no arquivo JSON 'metadata_index.json'
          +---> A partir deste milésimo de segundo, o arquivo está logicamente morto no cluster (Linearidade)
          +---> Coordenador ativa seu componente 'Node Client Interno':
          |       |
          |       |--- Envia gRPC privado [PurgeFile] para Node1 -> Apaga fisicamente os chunks e limpa pastas locais vazias.
          |       |--- Envia gRPC privado [PurgeFile] para Node2 -> Apaga fisicamente os chunks e limpa pastas locais vazias.
          |       |--- Envia gRPC privado [PurgeFile] para Node3 -> Apaga fisicamente os chunks e limpa pastas locais vazias.
          +---> Libera Lock de Escrita
          |
[Coordenador] ---> Devolve gRPC [DeleteResponse] confirmando sucesso da invalidação lógica global ---> [CLI]
  |
[CLI] ---> Imprime mensagem de confirmação de exclusão do arquivo e liberação de espaço físico no cluster.
```

---

### 10.4 Rastreamento Completo do Fluxo WORDCOUNT (Computação MapReduce Paralela) `[Não Feito]`

```text
[Operador CLI] ---> Executa comando wordcount /docs/documento.txt "concorrência"
  |
  +---> CLI emite requisição gRPC [LaunchMapReduceRequest] ---> [Coordenador (Porta 50051)]
          |
          +---> Coordenador (Master) intercepta requisição analítica
          +---> Interroga 'metadata_service' para capturar mapa físico de localidade do arquivo '/docs/documento.txt'
          +---> Descobre a distribuição física real dos blocos lógicos nos discos dos servidores
          +---> Ativa o 'MapReduce Service' e inicia o paralelismo de despacho focado em LOCALIDADE DE DADOS:
          |       |
          |       |--- Envia gRPC privado [RunMapTask] para Node1 -> Passa ID do Chunk 0 e o termo "concorrência".
          |       |--- Envia gRPC privado [RunMapTask] para Node2 -> Passa ID do Chunk 1 e o termo "concorrência".
          |
          +===> EXECUÇÃO CONCORRENTE DA FASE MAP (Nos Servidores Workers de Armazenamento):
          |       | [Node1 Local Storage] -> Abre chunk local em disco, lê bytes em RAM, conta string -> Encontra 12 termos.
          |       | [Node2 Local Storage] -> Abre chunk local em disco, lê bytes em RAM, conta string -> Encontra 18 termos.
          |       |
          |       |<--- Node1 devolve para o Coordenador resposta leve contendo o Inteiro: 12 (Overhead de Rede Zero!)
          |       |<--- Node2 devolve para o Coordenador resposta leve contendo o Inteiro: 18 (Overhead de Rede Zero!)
          |
          +===> FASE REDUCE MESTRE (No Plano de Controle do Coordenador):
          |       - Coordenador intercepta as respostas numéricas escalares leves vindas da malha de nós.
          |       - Executa a função de redução aritmética agregadora: Soma(12 + 18) -> Resultado Consolidado = 30.
          |
[Coordenador] ---> Retorna gRPC [LaunchMapReduceResponse] contendo a soma consolidada 30 ---> [CLI]
  |
[CLI] ---> Renderiza no terminal do usuário o resultado métrico final da computação distribuída paralela.
```

---

## 🛠️ 11. Decisões de Projeto e Alinhamento Técnico Perante Teorema CAP

A engenharia de software aplicada no design do DFS no Marco 3 foi norteada por decisões arquiteturais rígidas de alinhamento com os teoremas clássicos de computação distribuída, especificamente o **Teorema CAP (Consistency, Availability, Partition Tolerance)** de Eric Brewer.

- **Escolha Inegociável pelo Quadrante CP (Consistency and Partition Tolerance):** O design de arquitetura adotado no sistema prioriza de forma absoluta a Consistência Forte (C) e a Tolerância a Partições de Rede (P). Em sistemas distribuídos reais que operam sob redes não confiáveis, a ocorrência de partições de rede (onde nós ficam isolados ou mensagens caem) é uma inevitabilidade estatística. Diante de uma divisão de rede, o ecossistema do DFS prefere sacrificar a Disponibilidade total (A) de nós degradados a expor informações corrompidas ou obsoletas. Se o cluster sofrer falhas que inviabilizem o atingimento matemático dos limites de quóruns mínimos ($W=2$ ou $R=2$), o sistema prefere bloquear de forma segura as operações de escrita ou leitura dos usuários a permitir a escrita de dados divergentes (*split-brain*) ou leituras sujas.
- **Remoção Absoluta de Gargalos por Descentralização de I/O:** Ao abdicar do modelo clássico centralizado de proxying de arquivos (onde os arquivos passam obrigatoriamente pelas mãos de um servidor mestre antes de irem para os discos finais), o DFS mitigou o problema histórico de saturação de links de rede da controladora central. O Coordenador gerencia estritamente o Plano de Controle do sistema de arquivos, viabilizando que o Plano de Dados flua de forma limpa na periferia do ecossistema distribuído, escalando horizontalmente o throughput de rede do cluster de forma ilimitada conforme novas instâncias de armazenamento são acopladas.
- **Otimização Drástica de Banda por Meio de Localidade de Dados (`Data Locality`):** A arquitetura conceitual da engine de MapReduce integrada ao ecossistema combate o desperdício de infraestrutura física de rede. Em vez de trafegar arquivos de múltiplos gigabytes através de links de rede saturados para processá-los centralizadamente na máquina cliente ou no nó mestre, o sistema envia instruções lógicas leves contendo funções computacionais em direção às CPUs dos servidores remotos onde as frações de dados residem fisicamente em disco rígido. Os nós processam a informação localmente e devolvem apenas vetores numéricos escalares leves, poupando a malha de rede do cluster.

---

## 🧪 12. Critérios Técnicos Atendidos no Marco 3

- Implementação completa e funcional de rede distribuída nativa rodando sobre canais multiplexados gRPC e transporte estável HTTP/2.
- Definição estrita, tipada e agnóstica de contratos de interface baseada em especificações formais do Protocol Buffers (IDL).
- Descentralização real de tráfego pesado com isolamento arquitetural absoluto entre Control Plane e Data Plane.
- Mecanismo funcional estável de Replicação Ativa de Dados síncrona/concorrente adotando Fator de Replicação fixo $N=3$.
- Garantia de Consistência Forte e linearidade de dados em tempo real implementada sob Modelo de Quórum Estrito ($W=2, R=2$).
- Controle de Versionamento Global e Atômico carimbado de forma imutável junto à persistência física dos chunks binários nos discos dos nós.
- Cliente espesso interativo estável operando com cache persistente de canais gRPC quentes para mitigação de latência de handshakes TCP.
- Algoritmo matemático de Sharding determinístico e homogêneo baseado em espalhamento por Hashing de caminhos e IDs de chunks lógicos.
- Rotinas autônomas de limpeza física de storage local nos nós com remoção recursiva automática de diretórios vazios residuais.

---

## ⚠️ 13. Matriz de Tratamento de Falhas, Exceções e Resolução de Erros Críticos

- **Esquecimento de Recompilação do Arquivo Protobuf:** Caso o engenheiro altere a assinatura das mensagens ou adicione um método RPC no arquivo `dfs.proto` e esqueça de invocar o comando de geração automatizada `grpc_tools.protoc`, as novas propriedades não serão instanciadas nos pacotes Python internos. O interpretador disparará exceções fatais de atributo (`AttributeError: module 'dfs_pb2' has no attribute...`). **Resolução:** Recompile imediatamente o contrato IDL invocando a linha de comando contida no Step 4 do guia operacional.
- **Exceções Críticas de Tamanho de Payload (`RESOURCE_EXHAUSTED`):** Se o operador tentar efetuar o upload de um chunk substancialmente volumoso e a CLI reportar erro fatal de esgotamento de recursos do gRPC, significa que as diretivas de sintonia fina foram omitidas na inicialização de algum canal ou servidor do cluster. **Resolução:** Certifique-se de que a variável global `GRPC_OPTIONS` contida em `dfs/config.py` está sendo importada e injetada de forma adequada tanto no inicializador do servidor do Coordenador quanto na criação dos canais de stubs dos clientes.
- **Falha de Inicialização por Portas Sockets Ocupadas (`Address already in use`):** Processos fantasmas ou zumbis remanescentes de simulações e execuções passadas do DFS podem reter indevidamente a posse dos sockets de rede lógicos utilizados pelo cluster (portas 50051 a 50054). Caso ocorra erro de vinculação de endereço (*bind error*) na subida do sistema, limpe a pilha de subprocessos zumbis do seu sistema operacional utilizando comandos de kill industrial no terminal (ex: `pkill -f python` ou limpando a árvore de tarefas pelo gerenciador do Windows).
- **Inabilidade Crítica de Atingir Quórum Mínimo de Operações:** Se dois ou mais nós de armazenamento do cluster caírem ou ficarem permanentemente offline devido a falhas catastróficas, as requisições lógicas de escrita (`put`) ou leitura (`get`) da CLI falharão de forma segura. A interface reportará falha crítica de impossibilidade de atingimento de quórum estável. Esse comportamento é a comprovação prática de que a segurança do quadrante CP do Teorema CAP está operando com sucesso absoluto, blindando o sistema contra corrupção e inconsistência de dados.

---

## 📌 14. Próximos Passos e Desafios de Engenharia de Sistemas Distribuídos

A consolidação do Marco 3 pavimenta o caminho e assenta as bases tecnológicas definitivas para as seguintes expansões de nível industrial em marcos subsequentes do projeto:

- **Implementação de Detector de Falhas Descentralizado por Heartbeats Dinâmicos:** Criação de rotinas em background que disparam mensagens de pulso síncronas periódicas entre o Coordenador e os Workers para rastrear e catalogar a vitalidade dos nós em tempo real.
- **Mecanismos Autônomos de Auto-Recuperação (Self-Healing via Re-replicação de Chunks):** Desenvolvimento de algoritmos que, ao detectarem a morte definitiva de um nó de armazenamento, recalculam os shards perdidos e comandam os nós sobreviventes a duplicarem os blocos remanescentes entre si, restabelecendo o fator de replicação mestre $N=3$ sem intervenção humana.
- **Protocolos de Consenso Estruturados para Alta Disponibilidade do Coordenador (ex: Raft):** Eliminação do Coordenador como ponto único de falha lógica (*Single Point of Failure*) através da instanciação de um anel coordenado de masters operando sob o algoritmo de consenso Raft para eleição de líder e replicação de metadados.
- **Algoritmos Assíncronos de Rebalanceamento Automático de Carga de Disco:** Mecanismos que analisam a volumetria de ocupação de armazenamento físico de cada nó e movem blocos de dados de storages sobrecarregados para servidores ociosos, mantendo a homogeneidade do cluster de forma dinâmica.

---

## 👨‍💻 15. Observações Finais de Restrição

- O ecossistema opera sob Sharding determinístico baseado em Hashing puro por Chunk, dispensando o acoplamento de bancos de dados relacionais complexos ou pesados para mapeamento físico de localização de blocos no cluster.
- O caminho local do arquivo de origem na máquina hospedeira do usuário deve obrigatoriamente existir com permissões de leitura válidas antes de invocar comandos operacionais de injeção (`put`).
- O caminho virtual definido para um arquivo dentro da árvore lógica do DFS pode ser completamente independente do nome original ou localização real do arquivo físico em sua máquina hospedeira, operando como uma camada abstrata pura de visualização de dados.

---

## 👨‍💻 16. Autores

- **Higor Ferreira Silva** — Matrícula: 202201635
- **Vitória Mendonça** — Matrícula: 202004699
