# Sistema de Arquivos Distribuído (DFS)

Implementação de um Sistema de Arquivos Distribuído em Python, com gRPC e Apache Kafka, desenvolvido para a disciplina de Sistemas Distribuídos 1.

Disciplina: Sistemas Distribuídos 1\
Prof. Vagner José Sacramento Rodrigues\
Engenharia de Computação, EMC/UFG\
Autoria: Vitória Mendonça e Higor Ferreira Silva

Este documento é a referência técnica principal do projeto. Ele descreve o que o sistema faz, como ele está construído, como executá-lo, e a justificativa das decisões de projeto. Para o detalhamento aprofundado de cada mecanismo interno, consulte o `ARQUITETURA.md`.

---

## Sumário

1. [O que é o sistema](#1-o-que-é-o-sistema)
2. [Atendimento aos requisitos da especificação](#2-atendimento-aos-requisitos-da-especificação)
3. [Como o sistema está organizado](#3-como-o-sistema-está-organizado)
4. [Como executar](#4-como-executar)
5. [Como usar a interface (CLI)](#5-como-usar-a-interface-cli)
6. [Como testar e validar](#6-como-testar-e-validar)
7. [Decisões de projeto e justificativas](#7-decisões-de-projeto-e-justificativas)
8. [Comparação de alternativas e trade-offs](#8-comparação-de-alternativas-e-trade-offs)
9. [Estrutura de diretórios](#9-estrutura-de-diretórios)
10. [Divisão de responsabilidades da dupla](#10-divisão-de-responsabilidades-da-dupla)

---

## 1. O que é o sistema

Este projeto é um Sistema de Arquivos Distribuído, um software que armazena arquivos espalhados por vários computadores em vez de um só, replicando cada arquivo para sobreviver a falhas e permitindo que o conjunto cresça com a adição de novas máquinas. O sistema segue o modelo de coordenação centralizada com dados distribuídos, o mesmo desenho dos sistemas que inspiraram o trabalho, o Google File System e o Hadoop HDFS.

O sistema é formado por três tipos de participante. Um **coordenador** único centraliza todo o controle, guardando os metadados, decidindo onde cada pedaço de arquivo será gravado e supervisionando a saúde dos nós, sem nunca tocar nos bytes dos arquivos. Um conjunto de **nós de armazenamento** guarda fisicamente os pedaços, replica-os entre si e serve como porta de entrada e de saída de dados. Um ou mais **clientes**, através de uma interface de linha de comando, leem e gravam arquivos conversando com o coordenador para descobrir o que fazer e com os nós para transferir os bytes.

A propriedade central da arquitetura é que os dados pesados fluem diretamente entre o cliente e os nós, sem passar pelo coordenador. Esse desenho, chamado de separação entre plano de controle e plano de dados, é o que permite ao sistema escalar, porque o coordenador nunca se torna um gargalo de banda.

Os parâmetros padrão do cluster são cinco nós de armazenamento, fator de replicação igual a três e pedaços de quatro megabytes.

---

## 2. Atendimento aos requisitos da especificação

Esta seção faz o paralelo direto entre o que o professor pediu e o que o sistema entrega. A nota final é composta por correção funcional (20%), tolerância a falhas (20%), escalabilidade demonstrada (20%), qualidade do código (15%), documentação técnica (15%) e análise experimental (10%). O sistema foi construído para atender a cada um desses eixos.

### 2.1. Armazenamento distribuído e operações básicas

A especificação exige que os arquivos sejam distribuídos entre múltiplos nós e que o sistema suporte as operações de escrita, leitura, remoção e listagem. O sistema fatia cada arquivo em chunks de quatro megabytes e os distribui pelos cinco nós segundo a regra de posicionamento. As quatro operações estão implementadas na CLI como `put`, `get`, `rm` e `list`, e foram validadas com verificação byte a byte do arquivo após o ciclo completo de envio e recebimento.

### 2.2. Replicação de dados

A especificação pede replicação entre nós distintos, definição explícita do fator de replicação e estratégia de posicionamento. O fator de replicação é explícito e configurável (`REPLICATION_FACTOR = 3` em `config.py`). Cada chunk é replicado em três nós distintos. A estratégia de posicionamento é uma regra determinística de rodízio sobre o índice do chunk, descrita na seção 7.

### 2.3. Consistência de dados

A especificação exige a escolha de uma estratégia de consistência com justificativa formal. O sistema adota **consistência eventual**, garantida por quórum de escrita no momento do upload e por convergência automática na recuperação e na limpeza. A justificativa completa, incluindo a discussão do teorema CAP, está na seção 7.

### 2.4. Detecção e tratamento de falhas

A especificação cita heartbeat, timeout e retry, além de estratégias de detecção e de reconfiguração do cluster. O sistema implementa batimentos cardíacos periódicos, classificação dos nós por timeout em uma máquina de três estados (vivo, suspeito, morto), e reconfiguração automática do cluster pela re-replicação após a detecção de uma morte.

### 2.5. Balanceamento de carga

A especificação pede distribuição equilibrada de dados e requisições, citando hashing consistente ou estratégias similares. A regra de rodízio determinístico distribui os chunks de forma uniforme pelos nós, deslocando uma posição a cada chunk, o que evita concentração de carga nos primeiros nós.

### 2.6. Recuperação de dados

A especificação exige re-replicação automática após falhas e reconstrução de dados a partir de réplicas. Quando um nó morre, um supervisor no coordenador detecta a transição, identifica os chunks que perderam réplica e comanda a cópia a partir de uma réplica viva para um nó de destino, restaurando o fator de replicação sem intervenção manual.

### 2.7. Interface de acesso e documentação

A especificação aceita API, CLI ou interface simples, com documentação de uso. O sistema oferece uma CLI completa sobre gRPC, e este documento, junto do `ARQUITETURA.md`, é a documentação de uso.

### 2.8. Requisitos de escalabilidade obrigatórios

A especificação exige adição e remoção dinâmica de nós e testes de carga variando número de nós, volume de dados e taxa de requisições, com análise de latência, throughput, identificação de gargalos e discussão dos limites. O sistema implementa a adição dinâmica de nós em tempo de execução. Possui um arcabouço de benchmark automatizado que mede latência e throughput para volumes variados de dados, e a análise experimental correspondente identifica o gargalo da replicação síncrona e a variância imposta pelo ambiente de máquina única.

### 2.9. Requisitos de tolerância a falhas obrigatórios

A especificação exige simulação de queda de nós, perda de conectividade e atraso de mensagens, mecanismos de recuperação automática e garantias explícitas de disponibilidade e integridade. O sistema cobre a queda de nós (com re-replicação), tem um teste dedicado de atraso de rede, garante disponibilidade ao continuar operando com as réplicas vivas, e garante integridade pela imutabilidade dos chunks e pela verificação de hash nos testes.

### 2.10. Diferenciais (bônus)

A especificação valoriza o uso de containers, orquestração e observabilidade. O sistema usa Docker e Docker Compose para a infraestrutura de mensageria, e oferece um hub de telemetria que consome métricas em tempo real via Kafka.

---

## 3. Como o sistema está organizado

O sistema separa rigorosamente **quem decide** de **quem transporta**. Essa separação, entre plano de controle e plano de dados, é a decisão que organiza todo o resto.

O **plano de controle** vive no coordenador. Ele responde perguntas como onde gravar um arquivo, quais nós estão vivos e qual nó deve servir uma leitura. Trafega apenas metadados leves, mas concentra toda a inteligência. O **plano de dados** vive nos nós de armazenamento. Ele apenas transporta e guarda bytes, sem tomar decisões, e está distribuído por todos os nós.

A comunicação entre os participantes usa gRPC, com o contrato definido em arquivos Protocol Buffers. Há quatro serviços. O `ControlService`, no coordenador, oferece as oito chamadas de controle (registro de nó, batimento, pedido e confirmação de upload, pedido de download, remoção, listagem e atualização de réplicas). O `FileService`, nos nós, transfere arquivos de e para o cliente em streaming. O `ReplicationService`, entre nós, mantém as réplicas (gravar, buscar, apagar e listar chunks). O `DataPlaneService` instala nos nós os planos de execução de cada operação.

O fluxo de uma escrita ilustra os dois planos cooperando. O cliente pede ao coordenador permissão para gravar e recebe o plano de posicionamento. Transfere o arquivo, em streaming, ao nó de entrada escolhido. Esse nó fatia o arquivo em chunks e dispara as réplicas em paralelo aos outros nós, esperando a confirmação de pelo menos duas das três réplicas (o quórum de escrita). Concluída a transferência, o cliente confirma, e só então o coordenador registra o arquivo nos metadados.

A descrição completa de cada mecanismo, incluindo a regra de posicionamento, os fluxos de leitura e remoção, a detecção de falhas, a re-replicação, a coleta de lixo e a elasticidade, está no `ARQUITETURA.md`.

---

## 4. Como executar

### 4.1. Pré-requisitos

O sistema precisa de Python na versão 3.10 ou superior e de Docker com Docker Compose, usado para subir a infraestrutura de mensageria Kafka. No Windows, o Docker Desktop precisa estar aberto antes de iniciar o cluster.

### 4.2. Instalação das dependências

A partir da pasta `DFS`, instale as bibliotecas Python necessárias. As dependências são o gRPC e suas ferramentas, o Protocol Buffers e o cliente Kafka. O cliente Kafka usado é o `kafka-python-ng`, um fork mantido e síncrono, compatível com versões novas do Python.

```
cd DFS
pip install grpcio grpcio-tools protobuf kafka-python-ng
```

### 4.3. Subir o cluster completo

O projeto possui um orquestrador que automatiza todo o processo de inicialização. A partir da pasta raiz do projeto (onde está o `run_cluster.py`):

```
python run_cluster.py
```

Este comando executa quatro etapas em sequência. Primeiro faz uma checagem preventiva das portas usadas pelo projeto. Em seguida sobe o Zookeeper e os brokers Kafka via Docker, esperando dinamicamente até o broker estar pronto. Depois inicia o coordenador na porta 9100 e espera a porta gRPC ficar ativa. Por fim dispara os cinco nós de armazenamento, nas portas 9101 a 9105, que se registram no coordenador e assinam seus tópicos Kafka. Ao final, a janela exibe a mensagem de ecossistema operacional e deve permanecer aberta para monitorar as mensagens em tempo real. Para encerrar o cluster e limpar os containers, use Ctrl+C nessa janela.

### 4.4. Observação sobre o ambiente de execução

Na configuração de desenvolvimento, todos os processos rodam como instâncias independentes na mesma máquina, em portas distintas. Essa configuração preserva a independência de processo que caracteriza um sistema distribuído real, com cada nó tendo seu próprio servidor gRPC e seu próprio diretório em disco, comunicando-se exclusivamente por rede. O sistema também pode ser executado de forma genuinamente distribuída, com nós em máquinas diferentes na mesma rede local, bastando configurar os endereços anunciados.

---

## 5. Como usar a interface (CLI)

Com o cluster no ar, abra um segundo terminal e use a interface de linha de comando. Ela é executada por `run_cli.py`, a partir da pasta raiz do projeto.

### 5.1. Comandos disponíveis

Enviar um arquivo local para um caminho lógico no DFS:

```
python run_cli.py put caminho/local/arquivo.bin /destino/arquivo.bin
```

Baixar um arquivo do DFS para o disco local:

```
python run_cli.py get /destino/arquivo.bin arquivo_baixado.bin
```

Listar os arquivos armazenados:

```
python run_cli.py list
```

Remover um arquivo:

```
python run_cli.py rm /destino/arquivo.bin
```

### 5.2. Sessão interativa

A CLI também oferece uma sessão interativa, em que um único canal com o coordenador é aberto e reutilizado para toda a sessão, evitando o custo de reconectar a cada comando:

```
python run_cli.py menu
```

---

## 6. Como testar e validar

O projeto inclui scripts de teste e simulação, conforme a especificação exige nos entregáveis.

### 6.1. Teste de elasticidade

O teste de adição dinâmica valida, de forma isolada e em memória, a lógica de promoção de um nó novo à membership do cluster. Ele roda em segundos, sem necessidade de subir o cluster inteiro:

```
cd DFS
python test_elasticity_addition.py
```

A saída de sucesso confirma que um nó inédito entra na membership e passa a receber posicionamento.

### 6.2. Teste de tolerância a falhas com integridade

O teste de falha de nó é o teste-manchete de integridade sob falha. Ele gera um arquivo, calcula seu hash, faz o upload, derruba um nó de armazenamento, faz o download e compara o hash. O sucesso prova que o dado sobrevive à morte de um nó sem corrupção, atendendo às garantias de disponibilidade e integridade.

### 6.3. Teste de atraso de rede

O teste de atraso de rede mede o tempo de operação sob latência simulada, controlada por uma variável de ambiente. Ele cobre o cenário de atraso de mensagens exigido pela especificação.

### 6.4. Benchmark de carga e geração de gráficos

O arcabouço de benchmark automatizado mede latência e throughput para vários tamanhos de arquivo, em múltiplas iterações, e grava os resultados em CSV. A partir da pasta raiz, apontando para o coordenador:

```
python benchmark_harness.py --port 9100 --sizes 1 5 10 25 50 --iter 3
```

Os gráficos de throughput e latência são gerados a partir do CSV pelo script de plotagem:

```
python plot_metrics.py
```

### 6.5. Hub de telemetria em tempo real

O hub de telemetria é um consumidor Kafka que escuta o tópico de métricas e exibe estatísticas ao vivo (mínimo, máximo e média) das operações. Ele demonstra a observabilidade do sistema:

```
python telemetry_hub.py
```

### 6.6. Verificação manual no disco e nos metadados

A correção do posicionamento e da replicação pode ser verificada diretamente. O índice de metadados em `data/metadata/metadata_index.json` mostra, para cada chunk, em quais nós suas réplicas estão. Os chunks físicos ficam em `data/nodes/nodeX/chunks/`. Conferir que o mesmo chunk aparece nos três nós que os metadados indicam é a prova visual da replicação.

---

## 7. Decisões de projeto e justificativas

A especificação exige justificar as decisões arquiteturais. Esta seção reúne as principais.

### 7.1. Separação entre plano de controle e plano de dados

Esta é a decisão central. Em um sistema de arquivos distribuído, o volume de dados transferidos é ordens de magnitude maior que o de metadados. Se o coordenador intermediasse os bytes, ele se tornaria o gargalo de toda a banda do cluster, e adicionar nós não aumentaria a vazão, porque tudo continuaria passando pelo mesmo ponto. Ao manter o coordenador fora do caminho dos dados, a vazão agregada cresce com o número de nós, pois cada nó adiciona a própria banda. Esse é o raciocínio que levou o GFS a separar o master dos chunkservers e o HDFS a separar o NameNode dos DataNodes.

### 7.2. Posicionamento determinístico e persistido

O posicionamento das réplicas usa uma regra determinística de rodízio: o chunk de índice `i` é colocado nos nós das posições `i`, `i+1` e `i+2` de uma lista ordenada de nós, dando a volta ao chegar ao fim. A escolha de uma regra determinística permite a qualquer componente recalcular onde um chunk deveria estar usando apenas o índice e o tamanho do cluster, o que simplifica a recuperação e a verificação de órfãos. O posicionamento é decidido uma vez, no momento da escrita, e persistido nos metadados, nunca recalculado. Isso é uma questão de correção: se o cluster mudasse de tamanho e o posicionamento de arquivos antigos fosse recalculado, o sistema procuraria os chunks no lugar errado.

### 7.3. Replicação síncrona com quórum na escrita

A replicação acontece de forma síncrona no momento do upload, com um quórum de escrita igual a dois. O nó de entrada só considera um chunk gravado quando ao menos duas das três réplicas confirmam. Isso torna a escrita mais lenta que a leitura, mas garante durabilidade no instante em que o arquivo passa a existir. A escrita sobrevive à perda de uma réplica durante o upload, porque com duas confirmações o quórum é atingido. O sistema só recusa a escrita se restar menos de duas réplicas vivas, porque abaixo disso não conseguiria garantir a durabilidade mínima.

### 7.4. Consistência eventual

O modelo de consistência é eventual. Na escrita, o quórum garante durabilidade imediata. Na recuperação e na limpeza, o sistema converge sozinho para o estado correto em poucos ciclos: após uma morte, a re-replicação restaura a terceira réplica, e após um nó voltar com cópias redundantes, a coleta de órfãos as remove. A escolha conversa com o teorema CAP. Diante de uma falha de nó, o sistema escolhe preservar a disponibilidade, continuando a aceitar escritas e a servir leituras com as réplicas vivas, em vez de travar à espera de consistência forte. A integridade é preservada porque os chunks são imutáveis e identificados por um nome único, então qualquer cópia de um chunk é idêntica a qualquer outra. Essa imutabilidade dispensa um controle de versões, porque não existe o problema de versões divergentes do mesmo chunk.

### 7.5. Detecção de falhas por heartbeat com classificação preguiçosa

A saúde dos nós é monitorada por batimentos periódicos, e o coordenador classifica cada nó por timeout em uma máquina de três estados. A classificação é preguiçosa, calculada sob demanda a partir do horário do último batimento, em vez de uma rotina anunciando estados o tempo todo. Os limiares de suspeita e de morte foram calibrados para o ambiente de máquina única, em que pausas de escalonamento de processos são reais e não devem ser confundidas com falha de nó.

### 7.6. Mensageria assíncrona com Kafka

Os fluxos de coordenação que não exigem resposta imediata, como a re-replicação após uma falha, usam Kafka como barramento assíncrono. A ideia é o desacoplamento: quem emite uma ordem não precisa que o destinatário esteja pronto naquele instante. A replicação no momento do upload, por contraste, não passa por Kafka, porque precisa de confirmação síncrona para garantir durabilidade antes de registrar o arquivo. Essa separação entre o caminho síncrono da escrita e o caminho assíncrono da cura é proposital.

---

## 8. Comparação de alternativas e trade-offs

A especificação exige comparar alternativas e analisar trade-offs clássicos. Esta seção reúne essa análise.

### 8.1. Consistência forte versus eventual

A consistência forte garantiria que toda leitura visse sempre a última escrita confirmada em todas as réplicas. O custo seria coordenação mais cara a cada operação e indisponibilidade sob falha, porque o sistema teria que travar até reconciliar todas as réplicas. A consistência eventual, que adotamos, dá disponibilidade alta e convergência garantida em poucos ciclos. Para o caso de uso de um sistema de arquivos cujos chunks são imutáveis, a consistência forte não traz benefício prático, porque não há versões divergentes a reconciliar. O equilíbrio certo para este trabalho é a consistência eventual.

### 8.2. Coordenador único versus replicado

Um coordenador replicado em alta disponibilidade, com um nó ativo e um reserva, eliminaria o ponto único de coordenação. O custo seria o risco de cérebro dividido (dois coordenadores se julgando ativos ao mesmo tempo) e a complexidade de um nó de journal para sincronizar o estado. Optamos pelo coordenador único, que é o modelo de referência do GFS e do HDFS na versão 1, com recuperação pela releitura do índice persistido ao reiniciar. Para o prazo do trabalho, a complexidade da alta disponibilidade seria desproporcional ao benefício.

### 8.3. Modelo de proxy versus modelo de gateway

No modelo de proxy, o coordenador intermediaria os bytes dos arquivos. Isso simplificaria o cliente, mas faria o coordenador ser o gargalo de toda a banda, anulando o ganho de escala horizontal. No modelo de gateway, que adotamos, os bytes fluem direto entre cliente e nós, e a vazão cresce com o número de nós. O trade-off de replicação versus custo aparece aqui também: ao fixar o fator de replicação em três, triplicamos o consumo de disco em troca de tolerar a perda de duas réplicas de um chunk sem perda de dado.

### 8.4. Latência versus consistência

Sob falha, o sistema escolhe baixa latência e disponibilidade em vez de consistência forte. Uma leitura é servida pela réplica viva mais próxima, sem esperar a confirmação de todas as réplicas. A imutabilidade dos chunks torna esse trade-off seguro, porque a réplica mais próxima contém exatamente o mesmo dado que qualquer outra.

### 8.5. Posicionamento por rodízio versus hashing consistente

O hashing consistente é a estratégia clássica para minimizar o remapeamento de dados quando o cluster muda de tamanho. Como o nosso posicionamento é persistido e nunca recalculado, o problema que o hashing consistente resolve não nos afeta. A regra de rodízio sobre uma lista ordenada de nós distribui a carga de forma igualmente uniforme e é mais simples de explicar e de auditar, o que a torna a escolha adequada para o nosso caso.

---

## 9. Estrutura de diretórios

A organização do código reflete a separação em planos e a divisão por papel técnico.

```
.
├── run_cluster.py          Orquestrador que sobe Docker, coordenador e nós
├── run_cli.py              Ponto de entrada da interface de linha de comando
├── docker-compose.yml      Infraestrutura Kafka e Zookeeper
├── benchmark_harness.py    Benchmark de carga (latência e throughput)
├── plot_metrics.py         Geração dos gráficos a partir do CSV
├── telemetry_hub.py        Consumidor Kafka de métricas em tempo real
└── DFS/
    ├── requirements.txt
    └── dfs/
        ├── config.py           Parâmetros centralizados do cluster
        ├── pb/                 Contratos Protocol Buffers (dfs.proto, dataplane.proto)
        ├── interface/          Adaptadores de rede gRPC
        │   ├── server.py           Coordenador (ControlService)
        │   ├── storage_node.py     Processo do nó de armazenamento
        │   ├── cli.py              Interface de linha de comando
        │   └── kafka_listener.py   Consumo de comandos via Kafka
        ├── application/        Lógica de aplicação
        │   ├── metadata_service.py Índice de metadados
        │   └── data_service.py     Transferência e fan-out de replicação
        ├── cluster/            Infraestrutura de cluster
        │   ├── node_registry.py    Registro de nós e máquina de estados
        │   ├── placement.py        Regra determinística de posicionamento
        │   ├── replication_watcher.py  Supervisor de re-replicação
        │   ├── control_client.py   Cliente do plano de controle
        │   └── kafka_publisher.py  Publicação de comandos via Kafka
        └── storage/
            └── local_storage.py    Leitura e escrita de chunks no disco
```

---

## 10. Divisão de responsabilidades da dupla

O trabalho foi dividido segundo a fronteira entre os planos, o que permitiu desenvolvimento em paralelo. A fronteira de integração entre os dois lados é o contrato Protocol Buffers e o esquema de eventos Kafka.

A **Vitória** foi responsável pelo plano de controle: o coordenador e os RPCs do `ControlService`, o registro de nós com a máquina de estados de vivacidade, a regra de posicionamento, o serviço de metadados, o supervisor de re-replicação e a documentação de arquitetura.

O **Higor** foi responsável pelo plano de dados: os nós de armazenamento, os serviços de transferência e replicação, a persistência local dos chunks, a infraestrutura Kafka e o arcabouço de benchmark.
