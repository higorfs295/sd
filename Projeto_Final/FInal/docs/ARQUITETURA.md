# ARQUITETURA.md - Sistema de Arquivos Distribuído (DFS)

> Documento de arquitetura do projeto. Descreve o sistema **como ele está implementado** na branch `feature/integracao`, após a integração do plano de controle com o plano de dados.
>
> Toda mudança no contrato `.proto`, na regra de posicionamento (`placement.py`) ou neste documento exige combinação prévia entre a dupla.

Disciplina: Sistemas Distribuídos 1\
Prof. Vagner José Sacramento Rodrigues\
Engenharia de Computação, EMC/UFG\
Autoria: Vitória Mendonça e Higor Ferreira Silva

---

## Sumário

1. [Visão geral](#1-visão-geral)
2. [Princípio organizador: plano de controle versus plano de dados](#2-princípio-organizador-plano-de-controle-versus-plano-de-dados)
3. [Componentes do sistema](#3-componentes-do-sistema)
4. [Modelo de dados: arquivos, chunks e metadados](#4-modelo-de-dados-arquivos-chunks-e-metadados)
5. [Contrato de comunicação: os serviços gRPC](#5-contrato-de-comunicação-os-serviços-grpc)
6. [Posicionamento de réplicas (placement)](#6-posicionamento-de-réplicas-placement)
7. [Fluxo de escrita (PUT)](#7-fluxo-de-escrita-put)
8. [Fluxo de leitura (GET)](#8-fluxo-de-leitura-get)
9. [Fluxo de remoção (DELETE) e listagem (LIST)](#9-fluxo-de-remoção-delete-e-listagem-list)
10. [Detecção de falhas: heartbeat e máquina de estados](#10-detecção-de-falhas-heartbeat-e-máquina-de-estados)
11. [Recuperação: re-replicação automática](#11-recuperação-re-replicação-automática)
12. [Coleta de lixo e consistência eventual](#12-coleta-de-lixo-e-consistência-eventual)
13. [Elasticidade: adição dinâmica de nós](#13-elasticidade-adição-dinâmica-de-nós)
14. [Modelo de consistência](#14-modelo-de-consistência)
15. [Infraestrutura de mensageria: Apache Kafka](#15-infraestrutura-de-mensageria-apache-kafka)
16. [Parâmetros de configuração](#16-parâmetros-de-configuração)
17. [Mapa do código-fonte](#17-mapa-do-código-fonte)
18. [Trade-offs e decisões de projeto](#18-trade-offs-e-decisões-de-projeto)
19. [Limites conhecidos do sistema](#19-limites-conhecidos-do-sistema)

---

## 1. Visão geral

Este projeto implementa um Sistema de Arquivos Distribuído capaz de armazenar arquivos espalhados por vários nós, replicá-los para tolerar falhas e crescer pela adição de novos nós em tempo de execução. A arquitetura segue o modelo de **coordenação centralizada com dados distribuídos**, o mesmo princípio dos sistemas de referência da área, o Google File System (GFS) e o Hadoop HDFS.

O sistema é composto por três tipos de participante:

Um **coordenador** único, que centraliza todo o controle. Ele guarda os metadados (quais arquivos existem, em quantos pedaços cada um foi dividido e em quais nós cada pedaço está replicado), decide onde cada pedaço será gravado e supervisiona a saúde dos nós. O coordenador **nunca toca nos bytes** dos arquivos do usuário.

Um conjunto de **nós de armazenamento** (cinco na configuração padrão), que guardam fisicamente os pedaços dos arquivos, replicam esses pedaços entre si e atuam como porta de entrada e de saída de dados quando o coordenador os designa para isso.

Um ou mais **clientes** (a interface de linha de comando, a CLI), que leem e gravam arquivos no disco local do usuário e conversam com o coordenador para descobrir o que fazer e com os nós para transferir os bytes.

A característica central da arquitetura é que o tráfego pesado, os bytes dos arquivos, flui **diretamente** entre o cliente e os nós, sem nunca passar pelo coordenador. O coordenador troca apenas metadados leves. Esse desenho é o que permite ao sistema escalar: como o coordenador não fica no caminho dos dados, ele não se torna um gargalo de banda, e cada nó novo adicionado soma capacidade de transferência ao conjunto.

**Parâmetros principais do cluster:** cinco nós de armazenamento, fator de replicação igual a três, pedaços (chunks) de quatro megabytes.

---

## 2. Princípio organizador: plano de controle versus plano de dados

A decisão arquitetural mais importante do projeto, e a que organiza todo o resto, é separar de forma rígida **quem decide** de **quem transporta**. Essa separação tem um nome consagrado na literatura de sistemas distribuídos: a distinção entre **plano de controle** (control plane) e **plano de dados** (data plane).

O plano de controle é a inteligência do sistema. Ele responde perguntas como "onde devo gravar este arquivo", "quais nós estão vivos agora" e "qual nó deve servir esta leitura". É leve em volume de tráfego, porque só movimenta metadados, mas é central em responsabilidade. No nosso projeto, o plano de controle vive inteiramente no coordenador.

O plano de dados é a força bruta do sistema. Ele apenas transporta e guarda bytes, sem tomar nenhuma decisão sobre onde as coisas vão. É pesado em volume de tráfego e distribuído por todos os nós. No nosso projeto, o plano de dados vive nos nós de armazenamento.

A tabela abaixo resume a separação:

| Aspecto | Plano de Controle | Plano de Dados |
|---|---|---|
| Onde vive | Coordenador | Nós de armazenamento |
| O que trafega | Metadados leves (kilobytes) | Bytes de arquivos (kilobytes a gigabytes) |
| Quem usa | A CLI (para controle) e os nós (para registro e heartbeat) | A CLI (para PUT e GET) e os nós entre si (para replicação) |
| Estilo de chamada gRPC | Unário (uma pergunta, uma resposta) | Streaming (fluxo contínuo de pedaços) |

Essa divisão técnica também foi a divisão de trabalho da dupla. O plano de controle ficou sob responsabilidade da Vitória e o plano de dados sob responsabilidade do Higor. A fronteira entre os dois lados é o contrato `.proto`, a regra de posicionamento em `placement.py` e os identificadores compartilhados (`upload_id`, `download_id` e `chunk_id`). Os dois lados só se comunicam através dessa fronteira bem definida, o que permitiu desenvolvimento em paralelo.

**Justificativa formal da escolha.** Em um sistema de arquivos distribuído, o volume de dados transferidos é ordens de magnitude maior que o volume de metadados. Se o coordenador intermediasse os bytes (o chamado modelo de proxy), ele se tornaria o gargalo de toda a banda do cluster, e adicionar nós não aumentaria a vazão total, porque tudo continuaria passando pelo mesmo ponto. Ao remover o coordenador do caminho dos dados, a vazão agregada do sistema cresce com o número de nós, pois cada nó novo adiciona a sua própria banda de entrada e saída de disco e de rede. Esse é exatamente o raciocínio que levou o GFS a separar o master dos chunkservers e o HDFS a separar o NameNode dos DataNodes. Comparamos as duas alternativas e descartamos o modelo de proxy precisamente porque ele anula o ganho de escala horizontal que a especificação exige.

---

## 3. Componentes do sistema

### 3.1. Cliente (CLI)

O cliente é um processo Python que roda no terminal do usuário. Ele lê arquivos do disco local para enviá-los ao sistema e grava no disco local os arquivos que recebe. É deliberadamente um **cliente fraco**: ele não fatia arquivos em pedaços, não decide posicionamento e não conhece a topologia do cluster. Toda a inteligência está no coordenador.

Por operação, o cliente realiza duas conversas distintas. Primeiro fala com o coordenador, pelo plano de controle, para descobrir o que precisa ser feito (por exemplo, qual nó usar como porta de entrada e qual será o identificador do upload). Em seguida fala diretamente com um nó, pelo plano de dados, para transferir os bytes. Durante uma sessão interativa, a CLI mantém um único canal aberto com o coordenador e o reutiliza para todos os comandos, o que evita o custo de reabrir conexão a cada operação.

Os comandos disponíveis na interface são `put` (enviar um arquivo local para um caminho lógico no DFS), `get` (baixar um arquivo do DFS para o disco local), `list` (listar os arquivos armazenados), `rm` (remover um arquivo) e `menu` (abrir a sessão interativa).

### 3.2. Coordenador

O coordenador é um processo Python único que hospeda o serviço de controle (o `ControlService`). Ele é o cérebro do sistema e mantém dois estados centrais.

O primeiro é o **catálogo de metadados**, que registra quais arquivos existem, em quantos chunks cada um foi dividido, o horário de criação e, para cada chunk, em quais nós suas réplicas estão. Esse catálogo é persistido em disco no formato JSON e é a fonte da verdade do sistema.

O segundo é o **registro de nós**, que combina uma membership canônica (a lista oficial de quais nós fazem parte do cluster) com um estado de vivacidade dinâmico, atualizado a cada batimento cardíaco recebido dos nós.

A partir desses dois estados, o coordenador decide o posicionamento dos chunks, designa qual nó atua como porta de entrada ou de saída em cada operação e comanda a remoção física dos dados quando um arquivo é apagado. Vale repetir a propriedade definidora: o coordenador nunca toca nos bytes dos arquivos do usuário.

### 3.3. Nós de armazenamento

Cada nó de armazenamento é um processo Python independente, com seu próprio servidor gRPC e seu próprio diretório em disco. Em um sistema distribuído real, cada nó rodaria em uma máquina diferente. Na nossa configuração de desenvolvimento, os cinco nós rodam como processos separados na mesma máquina, em portas distintas (de 9101 a 9105), o que preserva a independência de processo que caracteriza um sistema verdadeiramente distribuído.

Cada nó tem três papéis. Como **armazenador**, ele guarda os pedaços de arquivo em seu disco. Como **réplica**, ele recebe cópias de pedaços de outros nós e as guarda também. Como **gateway**, quando designado pelo coordenador, ele atua como ponto de entrada de um upload (recebendo o arquivo do cliente e distribuindo as réplicas) ou como ponto de saída de um download (reunindo os pedaços e entregando ao cliente). Além disso, cada nó envia periodicamente um batimento cardíaco ao coordenador para sinalizar que está vivo.

---

## 4. Modelo de dados: arquivos, chunks e metadados

### 4.1. Como um arquivo é representado

Um arquivo enviado ao sistema não é guardado inteiro em lugar nenhum. Ele é dividido em **chunks** de tamanho fixo, quatro megabytes cada. Um arquivo de vinte megabytes, por exemplo, vira cinco chunks. Cada chunk recebe um identificador único no formato `<upload_id>_chunk_<índice>`, onde o `upload_id` identifica a operação de envio e o índice marca a posição do pedaço dentro do arquivo. Esse identificador é também o nome do arquivo físico gravado no disco do nó, sem nenhuma extensão.

A divisão em chunks de tamanho fixo é o que torna possível distribuir um único arquivo por vários nós e o que torna a replicação granular: cada chunk é replicado de forma independente.

### 4.2. Onde os dados ficam

Os chunks físicos ficam exclusivamente nos diretórios dos nós, em `data/nodes/nodeX/chunks/`. O coordenador, por contraste, mantém em `data/metadata/` apenas o arquivo `metadata_index.json`, que é o índice. Não há um único byte de dado de usuário no diretório do coordenador. Essa separação física no disco é o reflexo direto da separação lógica entre plano de controle e plano de dados.

### 4.3. A estrutura dos metadados

O índice de metadados é uma estrutura **plana**, não uma árvore de diretórios. Cada arquivo é uma entrada identificada pelo seu caminho lógico (por exemplo `/demo/big.bin`). Para cada arquivo, o índice guarda o número de chunks, o horário de criação e, para cada chunk, a lista ordenada de nós que guardam suas réplicas. A ordem dessa lista importa: o primeiro nó da lista é o primário daquele chunk.

Essa simplicidade é uma escolha consciente. Como a especificação pede as operações `write`, `read`, `delete` e `list` sobre um espaço de nomes, e não uma hierarquia de pastas com navegação, um índice plano cumpre o requisito sem a complexidade de gerenciar uma árvore de diretórios.

---

## 5. Contrato de comunicação: os serviços gRPC

Toda a comunicação entre os participantes do sistema usa gRPC, conforme a especificação determina a partir do Marco 3. O contrato está definido nos arquivos `dfs.proto` e `dataplane.proto`, e é a fronteira formal entre o trabalho das duas pessoas da dupla. Há quatro serviços, organizados pelos dois planos.

### 5.1. ControlService (plano de controle, no coordenador)

Este é o serviço central do coordenador, com oito chamadas. Duas servem à gestão do cluster. `RegisterNode` permite que um nó se apresente ao coordenador, informando seu identificador, o endereço onde escuta e quanto espaço livre tem. `Heartbeat` é o batimento periódico pelo qual cada nó sinaliza que está vivo e, de quebra, informa quais chunks possui (o chamado block report).

As demais servem ao ciclo de vida dos arquivos. `RequestUpload` é a chamada em que o cliente pede para gravar um arquivo e recebe de volta o plano de escrita. `ConfirmUpload` é a confirmação que registra o arquivo nos metadados depois que os bytes foram efetivamente gravados. `RequestDownload` devolve ao cliente o mapa de onde está cada chunk para a leitura. `DeleteFile` apaga um arquivo. `ListFiles` lista os arquivos existentes. `UpdateChunkReplicas` é a chamada usada durante a recuperação, para atualizar nos metadados quais nós passaram a guardar um chunk depois de uma re-replicação.

### 5.2. FileService (plano de dados, nos nós, voltado ao cliente)

Este serviço transfere arquivos entre o cliente e o nó-gateway, usando streaming. `UploadFile` recebe do cliente um fluxo de pedaços e os grava. `DownloadFile` faz o caminho inverso, devolvendo ao cliente um fluxo de pedaços reunidos. O uso de streaming aqui é essencial: ele permite transferir arquivos grandes sem carregar tudo na memória de uma vez, processando o arquivo em fatias de sessenta e quatro kilobytes.

### 5.3. ReplicationService (plano de dados, entre nós)

Este serviço é a comunicação nó a nó, usada para manter as réplicas. `StoreChunk` recebe e grava uma cópia de um chunk vinda de outro nó (é o que o nó-gateway chama para espalhar as réplicas durante um upload). `FetchChunk` busca um chunk em outro nó (é o que o nó de saída chama durante um download quando não tem localmente algum pedaço que precisa entregar). `DeleteChunk` apaga uma cópia específica. `ListChunks` informa quais chunks o nó possui.

### 5.4. DataPlaneService (plano de dados, configuração de planos)

Este serviço, definido em `dataplane.proto`, permite ao coordenador instalar nos nós os planos de execução de uma operação. `SetUploadPlan` informa ao nó-gateway de um upload qual é a lista de réplicas para cada chunk. `SetDownloadPlan` informa ao nó de saída de um download de onde buscar cada pedaço.

---

## 6. Posicionamento de réplicas (placement)

### 6.1. A regra

O posicionamento das réplicas é o coração do balanceamento de carga do sistema, e é **determinístico**. Dado um chunk de índice `i`, suas três réplicas são colocadas nos nós das posições `i`, `i+1` e `i+2` de uma lista ordenada de nós, dando a volta ao chegar ao fim (operação de módulo sobre o tamanho do cluster). A lista de nós é ordenada de forma estável pelo sufixo numérico do identificador, de modo que `node1` vem antes de `node2`, e assim por diante. O primeiro nó da tripla é o primário daquele chunk.

Em um cluster de cinco nós, o chunk de índice zero vai para os nós 1, 2 e 3. O chunk de índice um vai para os nós 2, 3 e 4. O chunk de índice três vai para os nós 4, 5 e, dando a volta, o nó 1. Esse deslocamento de uma posição a cada chunk distribui a carga de forma equilibrada por todos os nós, em vez de concentrar tudo nos primeiros.

### 6.2. Por que é determinístico e persistido

A escolha de uma regra determinística traz uma propriedade valiosa: qualquer componente do sistema consegue recalcular onde um chunk deveria estar usando apenas o índice do chunk e o tamanho do cluster, sem precisar consultar uma tabela. Isso simplifica a recuperação e a verificação de órfãos.

Há, porém, uma distinção crucial. O posicionamento é decidido **uma única vez**, no momento da escrita, com base na membership canônica daquele instante, e é **persistido nos metadados**. Ele nunca é recalculado depois. A razão é de correção: se o cluster crescer ou um nó cair, recalcular o posicionamento de arquivos antigos faria o sistema procurar os chunks no lugar errado. Os chunks de um arquivo antigo permanecem exatamente onde foram gravados, e o índice de metadados é a autoridade sobre onde cada um está.

### 6.3. Relação com o requisito da especificação

A especificação pede "distribuição equilibrada de dados e requisições" e cita "hashing consistente ou similares" como estratégia. A nossa regra de módulo sobre uma lista ordenada de nós é uma estratégia da mesma família: ela mapeia chunks para nós de forma uniforme e previsível. Optamos por ela em vez de hashing consistente clássico porque, para um cluster de tamanho conhecido e com posicionamento persistido, a regra de módulo é mais simples de explicar e de auditar, e o problema que o hashing consistente resolve (minimizar o remapeamento quando o cluster muda de tamanho) não nos afeta, já que nunca remapeamos posicionamentos antigos.

---

## 7. Fluxo de escrita (PUT)

O envio de um arquivo acontece em uma sequência bem definida que mostra os dois planos trabalhando juntos.

O cliente lê o arquivo do disco local e chama `RequestUpload` no coordenador, informando o caminho lógico e o tamanho. O coordenador gera um `upload_id`, calcula o posicionamento de cada chunk pela regra determinística, escolhe entre os nós vivos qual será a porta de entrada (o ingress) e devolve esse plano ao cliente. É importante notar que o ingress é escolhido **apenas entre nós vivos**, para não direcionar o cliente a um nó morto.

O cliente então transfere o arquivo, em streaming, para o nó ingress, através de `UploadFile`. O ingress recebe os bytes, fatia em chunks de quatro megabytes e, para cada chunk, executa o **fan-out**: ele grava a sua própria cópia e dispara em paralelo chamadas `StoreChunk` para os outros nós que devem ter aquela réplica.

Aqui entra a tolerância a falhas no momento da escrita. O fan-out é governado por um **quórum de escrita** igual a dois. O ingress considera o chunk gravado com sucesso quando ao menos duas das três réplicas confirmam. Se um dos nós-réplica estiver morto, a chamada `StoreChunk` para ele falha, mas essa falha é capturada e tratada como uma confirmação a menos, sem derrubar o upload. Com duas réplicas vivas, o quórum é atingido e a escrita prossegue. O sistema só recusa a escrita se restar menos de duas réplicas vivas, porque abaixo disso ele não conseguiria garantir a durabilidade mínima e seria desonesto fingir que conseguiu.

Concluída a transferência, o cliente chama `ConfirmUpload`, e só então o coordenador registra o arquivo nos metadados, com a lista de réplicas efetivamente gravadas para cada chunk. A partir desse momento o arquivo existe oficialmente e pode ser listado e lido.

---

## 8. Fluxo de leitura (GET)

A leitura segue o modelo do GFS, em que o cliente reúne os pedaços a partir de um mapa fornecido pelo coordenador, e o coordenador atua apenas como servidor de metadados.

O cliente chama `RequestDownload` no coordenador, informando o caminho do arquivo. O coordenador consulta os metadados e devolve o mapa de chunks: para cada pedaço, em quais nós ele está, e qual nó foi escolhido como ponto de saída (o egress). O critério de escolha do egress favorece a localidade, ou seja, prefere um nó que já tenha localmente o maior número de chunks daquele arquivo, para minimizar buscas entre nós.

O cliente então pede o arquivo ao nó egress, através de `DownloadFile`, em streaming. O egress monta o arquivo na ordem correta. Os chunks que ele já tem localmente, ele lê do próprio disco. Os que faltam, ele busca nos peers através de `FetchChunk`. Conforme reúne os pedaços, ele os envia ao cliente em fluxo, e o cliente os grava no disco local na ordem certa, reconstruindo o arquivo original.

Esse desenho atende ao que o professor solicitou desde o Marco 2: o cliente reassembla no estilo GFS, o coordenador devolve o mapa de chunks, e o critério de seleção do nó de remontagem é a localidade dos dados.

---

## 9. Fluxo de remoção (DELETE) e listagem (LIST)

A remoção de um arquivo começa com a chamada `DeleteFile` ao coordenador. O coordenador percorre todos os chunks do arquivo e, para cada réplica de cada chunk, dispara uma chamada `DeleteChunk` ao nó correspondente, para apagar a cópia física. Em seguida remove a entrada do arquivo dos metadados.

A remoção é tratada como **melhor esforço**. Se algum nó que deveria ter uma réplica estiver morto no momento da operação, a chamada `DeleteChunk` para ele falha, mas isso não impede a remoção: o coordenador contabiliza a falha, segue apagando as demais cópias e remove o índice mesmo assim. As cópias que ficaram para trás em um nó morto não viram lixo permanente, porque são recuperadas depois pelo mecanismo de coleta de órfãos descrito na seção 12, quando aquele nó voltar.

A listagem, por `ListFiles`, é a operação mais simples: o coordenador consulta o índice de metadados e devolve a relação de arquivos, com a contagem de chunks e os nós envolvidos. Como toda a informação está no índice, a listagem não envolve nenhum nó de dados.

---

## 10. Detecção de falhas: heartbeat e máquina de estados

A saúde dos nós é monitorada por batimentos cardíacos. A cada intervalo fixo (dois segundos), cada nó envia um `Heartbeat` ao coordenador. Essa mensagem carrega também o block report, a lista de chunks que o nó possui fisicamente, o que alimenta a coleta de lixo.

O coordenador classifica cada nó em um de três estados, segundo o tempo decorrido desde o último batimento recebido. Enquanto os batimentos chegam dentro do prazo, o nó está **ALIVE** (vivo). Se o silêncio ultrapassa o limiar de suspeita, o nó passa a **SUSPECT** (suspeito). Se o silêncio ultrapassa o limiar de morte, o nó é dado como **DEAD** (morto).

A classificação é **preguiçosa**, ou seja, o coordenador não mantém uma rotina anunciando o estado de todos os nós o tempo todo. O estado é calculado sob demanda, a partir do horário do último batimento, quando alguém pergunta. Quem pergunta de forma sistemática é o supervisor de re-replicação, descrito na próxima seção, que acorda periodicamente para detectar transições.

Um detalhe importante de calibração merece registro, porque foi ajustado durante os testes ao vivo e é defensável. Os limiares de suspeita e de morte precisaram ser afrouxados em relação aos valores iniciais. A razão é que, no ambiente de desenvolvimento, todos os processos (o coordenador, os cinco nós e a infraestrutura de mensageria) compartilham uma única máquina. Sob carga, durante um upload pesado, a thread de batimento de um nó pode ficar sem tempo de CPU por alguns segundos, o que, com limiares apertados, gerava falsos positivos de morte. Afrouxar os limiares foi a decisão correta para o ambiente: ela ajusta a sensibilidade da detecção à realidade de uma máquina única, onde pausas de escalonamento ocorrem e não devem ser confundidas com falha real. Em um cluster de máquinas dedicadas, esses limiares poderiam ser mais agressivos.

Esse mecanismo cobre o que a especificação pede em detecção de falhas: heartbeat como sinal de vida, timeout como critério de classificação e uma estratégia clara de detecção.

---

## 11. Recuperação: re-replicação automática

Quando um nó morre, os chunks que ele guardava ficam com menos réplicas do que o fator de replicação exige. O sistema corrige isso sozinho, restaurando a redundância. Esse é o mecanismo de recuperação automática.

O supervisor de re-replicação (o `ReplicationWatcher`) é uma thread do coordenador que acorda periodicamente e verifica as transições de estado dos nós. Quando detecta que um nó passou para DEAD, ele descobre, consultando os metadados, exatamente quais chunks aquele nó guardava e que agora estão com réplica de menos. Para cada um desses chunks, ele determina um nó de destino vivo que ainda não tenha aquela cópia e resolve para qual nó-fonte (uma réplica viva existente) deve pedir a cópia.

A ordem de copiar é então enviada ao nó-fonte através da infraestrutura de mensageria Kafka, no formato de um comando de replicação. O nó-fonte recebe o comando, copia o chunk para o nó de destino através de `StoreChunk` e, ao concluir, chama `UpdateChunkReplicas` no coordenador para que os metadados passem a registrar o nó novo no lugar do nó morto. Ao fim desse ciclo, o chunk volta a ter três réplicas, todas em nós vivos, e o nó morto deixa de constar como detentor daquele chunk.

A latência total de detecção e início da recuperação é, no pior caso, a soma do limiar de morte com o intervalo de varredura do supervisor, na ordem de uma dezena de segundos. Esse mecanismo atende diretamente ao requisito de "re-replicação automática após falhas" e "reconstrução de dados a partir de réplicas".

---

## 12. Coleta de lixo e consistência eventual

A coleta de lixo (garbage collection) é o mecanismo que apaga do disco os chunks que não deveriam mais existir, os chamados órfãos. Um chunk vira órfão em duas situações: quando o arquivo a que ele pertence foi apagado enquanto o nó estava fora, ou quando o chunk foi re-replicado para outro nó e a cópia antiga tornou-se redundante.

O mecanismo se apoia no block report que viaja em cada heartbeat. A cada batimento, o nó informa ao coordenador quais chunks tem fisicamente. O coordenador compara essa lista com o que os metadados esperam que aquele nó tenha. Tudo o que está no disco do nó mas não consta nos metadados é candidato a órfão, e o coordenador responde ao heartbeat com uma lista de chunks a apagar. O nó, ao receber essa lista, apaga os arquivos correspondentes do disco.

A detecção tem duas camadas de proteção contra remoção indevida. A primeira evita apagar chunks de um upload que ainda está em andamento. A segunda exige que um chunk seja apontado como suspeito em dois ciclos de heartbeat consecutivos antes de ser confirmado para deleção, o que protege contra condições de corrida momentâneas.

Esse mesmo mecanismo resolve o que chamamos de corrida da réplica ressuscitada. Quando um nó morre, é re-replicado e depois volta à vida, ele retorna carregando cópias de chunks que, nesse meio-tempo, já foram copiados para outros nós. Essas cópias agora são redundantes. Em poucos ciclos de heartbeat, a coleta de órfãos as identifica e as apaga. Esse é o nosso modelo de consistência para esse caso: **consistência eventual**. O estado do nó que voltou converge para o estado correto em alguns ciclos, sem intervenção manual, exatamente como o GFS e o HDFS fazem a remoção de réplicas redundantes em segundo plano.

Há ainda um aprimoramento que torna a remoção mais imediata para o caso do DELETE com nó morto. O coordenador mantém uma estrutura de **deleções pendentes**: quando uma chamada `DeleteChunk` falha porque o nó está morto, os chunks que não puderam ser apagados ficam registrados, associados àquele nó. Assim que o nó volta e envia o primeiro heartbeat, o coordenador entrega essas deleções pendentes junto da resposta, e o nó apaga as cópias imediatamente, sem esperar os dois ciclos da detecção comum. Os dois caminhos, a deleção pendente e a detecção de órfãos, convergem para o mesmo resultado, e a deleção pendente apenas o torna mais rápido e explícito.

---

## 13. Elasticidade: adição dinâmica de nós

O sistema permite adicionar um nó novo ao cluster em tempo de execução, sem reiniciar nada. Esse é o requisito de elasticidade da especificação.

Para que um nó inédito entre no cluster, três coisas precisam acontecer, e o sistema as cobre. Primeiro, o nó precisa saber a própria identidade (em qual endereço escutar e em qual diretório gravar). Como um nó novo não está descrito na configuração estática, ele recebe esses dados por argumentos de linha de comando ao subir. Segundo, o nó precisa se anunciar ao coordenador, através de `RegisterNode`. Essa chamada é o que promove o nó novo à membership canônica do cluster, por meio de uma função do registro de nós que insere o nó na lista oficial de forma ordenada. Terceiro, a partir desse registro, os uploads seguintes passam a considerar o nó novo no cálculo de posicionamento.

A propriedade que torna essa adição barata é que ela acontece **sem reorganizar nada**. Os arquivos antigos, gravados quando o cluster era menor, permanecem exatamente onde estavam, porque o posicionamento deles está persistido nos metadados e nunca é recalculado. Apenas os uploads novos passam a usar o nó adicional. Isso diferencia "crescer o cluster" de "reparticionar o cluster": a primeira operação é barata e local, a segunda exigiria mover dados em massa. Nosso sistema cresce em custo constante, sem migração de dados, e a maquinaria de re-replicação já existente é a mesma que, no futuro, reconstruiria as réplicas em um cenário de rebalanceamento.

A lógica de promoção à membership é validada de forma isolada por um teste de unidade que sobe um registro de nós em memória, adiciona um nó inédito e verifica que ele entra na membership e passa a receber posicionamento. Esse teste roda em segundos, sem necessidade de subir o cluster inteiro.

---

## 14. Modelo de consistência

A especificação exige a escolha explícita de um modelo de consistência e a justificativa formal dela. O nosso modelo é **eventual**, com garantias específicas em cada operação.

Na escrita, a consistência é garantida por quórum no momento do upload. Um arquivo só é registrado nos metadados depois que ao menos duas das três réplicas de cada chunk confirmaram a gravação. Isso assegura que, no instante em que o arquivo passa a existir oficialmente, ele tem durabilidade suficiente para sobreviver à perda de uma réplica.

Na recuperação e na limpeza, a consistência é eventual. Depois da morte de um nó, há uma janela de alguns segundos em que um chunk fica com duas réplicas em vez de três, até a re-replicação restaurar a terceira. Depois que um nó volta à vida com cópias redundantes, há uma janela de alguns ciclos de heartbeat até a coleta de órfãos limpá-las. Em ambos os casos, o sistema converge sozinho para o estado correto, sem intervenção manual.

A justificativa da escolha conversa diretamente com o **teorema CAP**, que a especificação pede para discutir. Diante de uma partição ou de uma falha de nó, escolhemos preservar a **disponibilidade**: o sistema continua aceitando escritas e servindo leituras com as réplicas vivas, em vez de travar à espera de consistência forte entre todas as réplicas. A integridade dos dados é preservada porque os chunks são imutáveis e identificados por um nome único, então uma cópia de um chunk é sempre idêntica a qualquer outra cópia daquele mesmo chunk. Essa imutabilidade é o que nos dispensa de um controle de versões complexo: não existe o problema de duas versões divergentes do mesmo chunk, porque um chunk nunca é modificado, apenas criado, copiado ou apagado.

Comparando as alternativas, como a especificação exige, a consistência forte (em que toda leitura veria sempre a última escrita confirmada em todas as réplicas) traria coordenação mais cara a cada operação e indisponibilidade sob falha, sem benefício prático para o caso de uso de um sistema de arquivos cujos chunks são imutáveis. A consistência eventual nos dá disponibilidade alta e convergência garantida, que é o equilíbrio certo para este trabalho. O trade-off clássico de latência versus consistência foi resolvido a favor da latência e da disponibilidade, e o trade-off de replicação versus custo foi resolvido fixando o fator de replicação em três, que tolera a perda de duas réplicas mantendo o dado vivo.

---

## 15. Infraestrutura de mensageria: Apache Kafka

O sistema usa o Apache Kafka como barramento de mensagens assíncronas entre o coordenador e os nós para os fluxos de coordenação que não precisam de resposta imediata. A ideia central é o **desacoplamento**: quem emite uma ordem não precisa que o destinatário esteja pronto naquele exato instante, pois a mensagem fica no tópico até ser consumida.

Kafka carrega três tipos de fluxo no sistema. O primeiro é a re-replicação: quando o coordenador decide que um chunk precisa ser copiado, ele publica um comando no tópico do nó-fonte, que o consome e executa a cópia. O segundo é a drenagem de nó, um comando preparado para o cenário de saída ordenada. O terceiro é a telemetria, em que métricas de operação podem ser publicadas em um tópico próprio e consumidas por um painel de monitoramento.

Vale uma distinção que evita confusão. A replicação no momento do upload **não** passa por Kafka. Ela é síncrona e direta: o nó-gateway chama `StoreChunk` nos nós-réplica e espera o quórum, tudo por gRPC. Kafka entra apenas nos fluxos de coordenação posteriores, como a re-replicação após uma falha. Essa separação é proposital: a escrita precisa de confirmação imediata para garantir durabilidade antes de registrar o arquivo, enquanto a re-replicação pode ser assíncrona porque é uma cura de fundo.

A infraestrutura Kafka roda em contêineres Docker, orquestrados por Docker Compose, o que é um diferencial valorizado pela especificação. O uso de contêineres garante que a versão do Kafka seja idêntica em qualquer máquina que rode o projeto, eliminando o problema de divergência de ambiente.

---

## 16. Parâmetros de configuração

Os parâmetros do sistema ficam centralizados em `config.py`, o que permite ajustar o comportamento do cluster em um único lugar. Os principais são:

| Parâmetro | Valor | Significado |
|---|---|---|
| `NODE_COUNT` | 5 | Número de nós de armazenamento do cluster |
| `REPLICATION_FACTOR` | 3 | Quantas cópias de cada chunk são mantidas |
| `CHUNK_SIZE` | 4 MB | Tamanho de cada pedaço de arquivo |
| `STREAM_SIZE` | 64 KB | Tamanho da fatia transferida por mensagem de streaming |
| `PORT` | 9100 | Porta gRPC do coordenador |
| `BASE_NODE_PORT` | 9101 | Porta inicial dos nós (9101 a 9105) |
| `HEARTBEAT_INTERVAL` | 2 s | Intervalo entre batimentos de cada nó |
| `HEARTBEAT_SUSPECT` | limiar de suspeita | Silêncio acima do qual o nó vira SUSPECT |
| `HEARTBEAT_DEAD` | limiar de morte | Silêncio acima do qual o nó vira DEAD |
| `WATCHER_INTERVAL` | 2 s | Frequência com que o supervisor de re-replicação acorda |

Mudar a quantidade de nós do cluster é uma operação de um único parâmetro: alterar `NODE_COUNT` faz a função `build_nodes` gerar a configuração de todos os nós automaticamente. Os limiares de heartbeat foram afrouxados em relação aos valores iniciais para tolerar as pausas de escalonamento de uma máquina única, conforme explicado na seção 10.

---

## 17. Mapa do código-fonte

A organização dos arquivos reflete a separação em planos. Os principais módulos são:

No plano de controle, sob responsabilidade da Vitória: `server.py` hospeda o coordenador e implementa os RPCs do `ControlService`, além da estrutura de deleções pendentes. `node_registry.py` mantém o registro de nós, a membership canônica e a máquina de estados de vivacidade. `metadata_service.py` cuida do índice de metadados em JSON. `placement.py` contém a regra determinística de posicionamento. `replication_watcher.py` é o supervisor que detecta mortes e dispara a re-replicação.

No plano de dados, sob responsabilidade do Higor: `storage_node.py` é o processo do nó de armazenamento, com o servidor gRPC, o worker de heartbeat e a autoconfiguração para elasticidade. `data_service.py` implementa a transferência de arquivos e o fan-out de replicação com quórum. `local_storage.py` cuida da leitura e escrita dos chunks no disco. Os módulos de Kafka cuidam da publicação e do consumo de comandos de coordenação.

Compartilhados pelos dois planos: os arquivos `.proto` (`dfs.proto` e `dataplane.proto`) definem o contrato, `config.py` centraliza os parâmetros, `client.py` oferece os clientes de controle e de dados usados pela CLI, e `cli.py` implementa a interface de linha de comando. Os scripts `run_cluster.py` e `run_cli.py` sobem o cluster e a interface, respectivamente.

---

## 18. Trade-offs e decisões de projeto

Esta seção reúne as decisões de projeto e os trade-offs clássicos, conforme o nível de profundidade que a especificação exige.

**Coordenador único versus coordenador replicado.** Optamos por um coordenador único, que é o modelo de referência do GFS e do HDFS na versão 1. Um coordenador replicado em alta disponibilidade (ativo e reserva) traria o risco de cérebro dividido e a complexidade de um nó de journal para sincronizar o estado, o que seria desproporcional ao prazo do trabalho. A recuperação do coordenador, no nosso modelo, se dá pela releitura do índice de metadados persistido em disco, que é exatamente o que o GFS e o HDFS 1.x fazem. Essa foi uma decisão consciente de escopo, com a alternativa avaliada e descartada por justificativa de complexidade.

**Replicação síncrona na escrita versus assíncrona.** Escolhemos replicação síncrona com quórum no momento do upload. Isso torna a escrita mais lenta que a leitura, porque o ingress espera a confirmação de duas réplicas antes de retornar. O benefício é a durabilidade garantida no instante em que o arquivo passa a existir. A alternativa assíncrona seria mais rápida, mas registraria o arquivo antes de ter cópias suficientes, abrindo uma janela de perda de dado. Para um sistema de arquivos, priorizamos a durabilidade.

**Latência versus consistência.** Diante de falha, escolhemos disponibilidade e baixa latência sobre consistência forte, conforme discutido na seção 14. Os chunks imutáveis tornam esse trade-off seguro, porque não há risco de versões divergentes.

**Replicação versus custo.** O fator de replicação três é o equilíbrio entre durabilidade e uso de espaço. Ele triplica o consumo de disco, mas tolera a perda simultânea de duas réplicas de um chunk sem perda de dado, que é a garantia padrão de sistemas como o HDFS.

**Posicionamento determinístico persistido versus recálculo dinâmico.** Persistir o posicionamento e nunca recalculá-lo é o que torna a adição de nós barata e a recuperação simples, ao custo de não rebalancear automaticamente os dados antigos quando o cluster cresce.

---

## 19. Limites conhecidos do sistema

Documentar os limites com honestidade é parte da maturidade de engenharia que a especificação valoriza, e protege a dupla de surpresas na defesa.

**Remoção dinâmica intencional de nós.** O sistema implementa a adição dinâmica de nós, mas não a remoção intencional com drenagem prévia dos dados. O que o sistema trata é a **falha** de um nó, pela re-replicação, e não a sua saída ordenada. A maquinaria do lado do nó para receber uma ordem de drenagem existe, mas a orquestração de controle que a dispara e poda a membership não foi implementada. A decisão de escopo se justifica porque a adição de nós já demonstra a elasticidade exigida, e a re-replicação já demonstra a parte difícil, que é reconstruir réplicas em outros nós. Uma consequência observável disso é que um nó que entrou no cluster permanece na membership canônica mesmo depois de morto, o que faz os uploads seguintes ainda planejarem réplicas nele, falharem naquele nó específico e serem cobertos pelo quórum. Esse comportamento é correto dentro do modelo e é precisamente a evidência de por que a remoção intencional é um recurso à parte, e não um efeito colateral de matar um processo.

**Coordenador como ponto único de coordenação.** Com um coordenador único, a indisponibilidade dele interrompe novas operações de controle, embora os dados nos nós permaneçam intactos. A recuperação é a releitura do índice persistido ao reiniciar o coordenador.

**Ambiente de máquina única.** Como os experimentos rodam todos os processos em uma máquina, há contenção de CPU e de disco que não existiria em um cluster real de máquinas dedicadas. Isso se manifesta como variância nos tempos de operação para arquivos grandes e foi a razão do afrouxamento dos limiares de heartbeat. Os números de desempenho devem ser lidos com essa limitação de ambiente em mente.

**Controle de versões de chunks.** O sistema não implementa versionamento de chunks, e isso é uma escolha coerente, não uma omissão. Como os chunks são imutáveis e identificados de forma única, não existe o problema de versões divergentes que o versionamento resolveria. Introduzi-lo exigiria mudanças no contrato `.proto`, no formato dos metadados e no plano de dados, sem ganho para o modelo atual de chunks imutáveis.
