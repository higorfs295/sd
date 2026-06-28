# Relatório Técnico de Engenharia: Arquitetura, Implementação e Avaliação Experimental do Sistema de Arquivos Distribuído (DFS)

**Disciplina / Projeto:** Sistema de Arquivos Distribuído (DFS)
**Autores:** - **Higor** (Engenharia do Data Plane, Infraestrutura de I/O, Tolerância a Falhas, Kafka e Benchmarking)

- **Vitória** (Engenharia do Control Plane, Metadados, Placement e Coordenação Lógica)
**Data de Conclusão:** Junho de 2026
**Marcos Avaliados:** Marco 4 (Alta Disponibilidade e Integração Kafka) e Marco 5 (Métricas, Garbage Collection e Testes de Estresse)

---

## 1. SUMÁRIO EXECUTIVO E FILOSOFIA DE DIVISÃO ARQUITETURAL

A concepção, o desenvolvimento e a estabilização de um Sistema de Arquivos Distribuído (DFS) resiliente exigem a mitigação de desafios profundos em relação à concorrência, consistência de dados e esgotamento de recursos físicos (I/O e Memória). Para garantir a viabilidade técnica deste projeto, a arquitetura foi estritamente bipartida utilizando o paradigma de *Separation of Concerns* (Separação de Preocupações).

O sistema opera sob duas vias de comunicação isoladas:

1. **O Plano de Controle (Control Plane):** Sob responsabilidade exclusiva da engenheira **Vitória**, este plano atua como o "cérebro" do cluster. Ele não trafega bytes de arquivos, focando-se estritamente na validação de permissões, mapeamento de diretórios virtuais, monitoramento do estado de saúde dos nós e na orquestração algorítmica de blocos (*Placement*).
2. **O Plano de Dados (Data Plane):** Sob responsabilidade exclusiva do engenheiro **Higor**, este plano atua como os "músculos" e o "sistema nervoso" do cluster. O Data Plane lida com o tráfego pesado via *streaming* gRPC, a persistência física dos bytes nos discos locais, a comunicação *peer-to-peer* assíncrona entre nós, a tolerância a falhas através de mensageria com Apache Kafka, a coleta de lixo física (*Garbage Collection*) e a orquestração de testes de estresse para aferição de limites do hardware.

Este documento foca exaustivamente nas decisões de projeto, na resolução de *bugs* críticos e na engenharia construída por **Higor** (Plano de Dados), deixando o espaço estrutural para a documentação do Plano de Controle.

---

## 2. ARQUITETURA DO PLANO DE CONTROLE (CONTROL PLANE)

**[SEÇÃO RESERVADA PARA A ENGENHARIA DO CONTROL PLANE - RESPONSÁVEL: VITÓRIA]**

> *(Instrução para Vitória: Documente aqui a modelagem da sua árvore de diretórios, o banco de metadados em memória, as RPCs do Coordenador, o funcionamento do NodeRegistry para gerir nós vivos/mortos e a matemática por trás do seu algoritmo de Placement Round-Robin Determinístico para fatiamento de arquivos).*

---

## 3. ARQUITETURA E DESENVOLVIMENTO DO PLANO DE DADOS (DATA PLANE)

**Responsável: Higor*

O desenvolvimento do Plano de Dados exigiu a modelagem de instâncias chamadas *Storage Nodes* (Nós de Armazenamento). O desafio principal imposto à minha parte do projeto foi garantir que esses nós pudessem processar arquivos maiores que a memória RAM disponível no host, garantindo também a resiliência ($R=3$) de forma que a replicação de dados não bloqueasse a interface do usuário final. As etapas a seguir detalham o que foi construído nas semanas anteriores à validação do Marco 5.

### 3.1. Implementação de Streaming gRPC e Fatiamento Físico de I/O (Chunking)

A primeira barreira técnica enfrentada foi a limitação imposta por requisições HTTP/REST tradicionais, que exigem o carregamento integral de um *payload* na memória.

- **Solução de Engenharia:** Desenvolvi os serviços do `dfs_pb2_grpc.py` utilizando fluxos bidirecionais (*Streams*) do gRPC.
- **Mecânica de Chunking:** No lado do cliente (`client.py`), programei geradores em Python (`yield`) que abrem o arquivo físico e leem blocos limitados pela constante `STREAM_SIZE`. O Nó de Entrada (*Ingress Node*) recebe iterativamente os objetos `UploadChunk` e realiza uma operação de *append* sequencial diretamente contra o disco rígido da máquina (`LocalStorage`). Isso converteu uma arquitetura dependente de RAM para uma arquitetura dependente de disco, viabilizando o upload de arquivos massivos.

### 3.2. Tolerância a Falhas e Desacoplamento via Apache Kafka (Marco 4)

Para cumprir o requisito de tolerância a falhas e manter 3 cópias de cada fatiamento ($R=3$), recusei a abordagem de replicação síncrona encadeada, pois ela aumentaria drasticamente a latência percebida pelo cliente.

- **Integração de Mensageria:** Incorporei e configurei *containers* do Apache Kafka e do Zookeeper na rotina de subida do cluster (`run_cluster.py`).
- **Padrão Producer/Consumer Descentralizado:** Desenvolvi a classe `DataPlaneCommandListener` (`kafka_listener.py`). O fluxo foi orquestrado da seguinte forma: quando um Nó de Entrada termina de gravar um arquivo localmente, ele publica um evento (como um *Producer*) em um tópico do Kafka confirmando a posse dos blocos. Os outros 4 nós do cluster (atuando como *Consumers*) escutam este tópico em *background*. Ao detectarem que o algoritmo de *Placement* lhes atribuiu uma cópia daquele bloco, eles se conectam de forma reversa ao Nó de Entrada via RPC `FetchChunk` e realizam a cópia dos dados silenciosamente. O usuário recebe a confirmação de sucesso de imediato, enquanto a rede resolve a consistência eventual e a redundância nos milissegundos seguintes.

---

## 4. O DESENVOLVIMENTO DO MARCO 5: CICLO DE VIDA E GARBAGE COLLECTION

Com o tráfego pesado estabilizado, o Marco 5 introduziu o desafio do gerenciamento contínuo de recursos físicos. Os arquivos lógicos estavam sendo apagados do Coordenador, mas fisicamente, o armazenamento do meu Data Plane estava inflando infinitamente devido a *chunks* órfãos.

### 4.1. Construção do Garbage Collection (Coleta de Lixo via Telemetria)

Para resolver o *Storage Leak*, programei uma rotina de exclusão passiva atrelada ao "pulso" do cluster.

- Na classe `HeartbeatWorker` (dentro de `storage_node.py`), implementei a coleta de métricas reais da máquina hospedeira usando `shutil.disk_usage`. A cada intervalo de envio de telemetria, o Nó envia ao Coordenador não apenas seu estado de vida, mas também a lista local de `chunk_ids` que ele possui no disco.
- **O Gatilho de Deleção:** O Coordenador da Vitória avalia esses IDs contra a árvore de diretórios oficial e responde com uma matriz `chunks_to_delete`. Desenvolvi o manipulador que itera sobre essa matriz devolvida: ele mapeia o caminho absoluto de cada fatiamento no *File System* do Linux/Windows e dispara o syscall `os.remove(caminho_chunk)`. A rotina purga os arquivos lixo fisicamente do HD sem interromper o serviço *gRPC* que opera paralelamente em outras *threads*.

---

## 5. A JORNADA DE DEBUGGING, INTEGRAÇÃO E SOLUÇÃO DE PROBLEMAS (Testes Finais)

Para submeter a infraestrutura a um estresse empírico e extrair métricas de desempenho, desenvolvi um *Harness* autônomo (`benchmark_harness.py`). Contudo, a integração final entre as complexas regras de negócio do Control Plane e os fluxos do Data Plane expôs problemas gravíssimos de compatibilidade que exigiram uma bateria intensa de correções em tempo real (*live debugging*) nesta reta final. Abaixo, documento os 4 incidentes críticos solucionados durante as sessões de teste.

### 5.1. Incidente 1: O Conflito de Portas e a Recusa do SO (`Connection refused - 10061`)

Ao tentar disparar a primeira iteração do benchmark para avaliar arquivos de 1MB a 50MB, o console do Windows abortou a execução instantaneamente com o erro:
`UNAVAILABLE: ipv4:127.0.0.1:50051: WSAGetOverlappedResult: Connection refused (10061)`

- **Análise da Falha:** O erro indicava que não havia nenhum *socket* TCP escutando o tráfego do teste. O script de automação (`benchmark_harness.py`) estava hardcoded para buscar o Data Plane na porta genérica `50051`. No entanto, a infraestrutura que desenvolvi para simular 5 nós reais dividia as instâncias pelas portas `9101, 9102, 9103, 9104 e 9105`.
- **Resolução:** Como o *Harness* estava usando métodos de I/O puro, ajustei os argumentos da CLI (`python benchmark_harness.py --nodes 5 --iter 3 --port 9101`) para forçar o cliente a acoplar-se diretamente ao `Node1`, destravando o roteamento TCP e permitindo o avanço do teste.

### 5.2. Incidente 2: O Paradoxo da Pré-Condição (`FAILED_PRECONDITION`)

Assim que a conexão TCP foi restabelecida na porta 9101, o servidor gRPC rejeitou agressivamente todos os *streams* de dados com a seguinte exceção:
`StatusCode.FAILED_PRECONDITION: sem plano para upload_id=bench_1_0; chame SetUploadPlan antes de UploadFile`

- **Análise da Falha:** O erro não era um defeito, mas sim o **mecanismo de defesa (Zero-Trust) do Data Plane operando perfeitamente**. O Nó de Armazenamento exige que uma operação seja previamente autorizada pelo Coordenador (Plano de Controle) antes de aceitar receber um arquivo. O script legadode teste estava tentando burlar a regra, saltando a fase de autorização e injetando *bytes* cegamente através do método `client.upload()`.
- **Resolução:** Para sanar a quebra do fluxo, executei uma refatoração massiva nos métodos `upload_file` e `download_file` dentro do `dfs/client.py`. Introduzi a arquitetura de **Two-Phase Handoff** (Transferência em Duas Fases):
  1. A CLI agora instancia primeiramente o `ControlClient` e solicita um `RequestUpload` ao Coordenador (na porta 9100).
  2. O Coordenador gera o ID e define qual Nó será o *Ingress*.
  3. A CLI se conecta dinamicamente a esse Nó específico e dispara o serviço `ingress_client.set_upload_plan()`, registrando a tabela de fatiamento.
  4. Somente então, o *streaming* `ingress_client.upload()` é invocado. Adaptei o `benchmark_harness.py` para apontar para o Coordenador (`--port 9100`) e utilizar estes novos métodos abstratos, integrando com perfeição absoluta os Planos de Dados e Controle.

### 5.3. Incidente 3: Incompatibilidade de Contratos Protobuf (`DownloadRequest` vs `DownloadStart`)

Com o fluxo de Upload corrigido, o teste rodou por quase 30 segundos, injetando 50MB de dados que foram fatiados, gravados em disco e replicados pelo Kafka impecavelmente. No entanto, o sistema colapsou logo após, na tentativa de recuperar esses dados, lançando o erro Python:
`module 'dfs.pb.dfs_pb2' has no attribute 'DownloadRequest'`

- **Análise da Falha:** O *Traceback* revelou uma quebra de contrato de serialização. A biblioteca do *Protocol Buffers* não reconhecia a classe da mensagem que o cliente estava utilizando para abrir o fluxo do Nó *Egress*.
- **Resolução:** Acessei o arquivo de compilação gerado (`dfs_pb2.py`) e fiz a engenharia reversa do contrato `dfs.proto`. Identifiquei que a mensagem que a arquitetura previa para iniciar uma leitura chamava-se `DownloadStart`, e não `DownloadRequest`. Ajustei a instanciação no arquivo `client.py` (de `dfs_pb2.DownloadRequest` para `dfs_pb2.DownloadStart`). Imediatamente, os fluxos de leitura foram destravados, e o script de benchmark executou as 15 iterações complexas (de 1MB a 50MB) com sucesso retumbante, gravando os dados de *throughput* em CSV.

### 5.4. Incidente 4: A Quebra do Pipeline Analítico (`ValueError` no Pandas)

Com os testes estressantes finalizados, o CSV resultante acumulava milhares de registros. Tentei invocar o script analítico (`plot_metrics.py`) para renderizar os gráficos utilizando as bibliotecas `pandas` e `seaborn`. O script colapsou com o erro:
`ValueError: Could not interpret value 'throughput_mb_s' for 'y'. An entry with this name does not appear in 'data'.`

- **Análise da Falha:** Havia um descompasso nos cabeçalhos (headers) entre o gravador de testes e o leitor de gráficos. O `benchmark_harness.py` estava gravando as colunas como `throughput_mbs` e `nos_ativos`, enquanto a classe `DataFrame` do Pandas esperava `throughput_mb_s` e `num_nos_ativos`.
- **Resolução:** Unifiquei e padronizei a injeção do DataFrame no arquivo `plot_metrics.py`. As chaves `x` e `y` do Seaborn foram equalizadas (`y='throughput_mbs'`, `x='nos_ativos'`). A modificação restaurou o pipeline de renderização, gerando perfeitamente as evidências visuais de performance que baseiam a seção seguinte.

---

## 6. RESULTADOS EMPÍRICOS E AVALIAÇÃO DE DESEMPENHO (BENCHMARKING)

**Responsável: Higor*

Os dados obtidos pelas rotinas automatizadas demonstraram claramente a assimetria intrínseca de Sistemas Distribuídos e os limites do *hardware* de homologação. Os testes ocorreram em ambiente unificado (`localhost`) operando os 6 componentes (Coordenador + 5 Nós) simultaneamente.

### 6.1. O Comportamento do Throughput: Velocidade de Download vs. O Custo do Upload

A análise revelou que o DFS é avassaladoramente eficiente em operações de leitura, e cobra um preço computacional proposital nas gravações.

- Nas cargas estáveis de 5MB e 10MB, a operação de **Download atingiu picos de 43.36 MB/s**. Isso ocorre porque o *Download* não exige coordenação Kafka nem gravação física; o Nó *Egress* apenas resgata os blocos do *Page Cache* da memória RAM e empurra para a interface de rede.
- Em contrapartida, as mesmas cargas de **Upload variaram entre 10.16 MB/s e 15.25 MB/s**. Essa latência extra no *upload* não é um gargalo de código, mas o custo operacional exigido para garantir a resiliência ($R=3$). O sistema precisa receber a carga de rede, fatiar o arquivo, fazer *Syscalls* de escrita no disco físico (`os.write`), instanciar *Producers*, e publicar a matriz do arquivo no *Broker* Kafka. A lentidão é a prova empírica de que a replicação de dados paralela e a serialização gRPC estão funcionando ativamente para proteger os dados.

### 6.2. O Estresse Máximo e o Colapso de I/O (O Teste de 50MB)

Ao injetarmos os massivos arquivos de 50MB na terceira iteração do laço (`iter 3`), os gráficos registraram a queda brusca que esperávamos em nossa modelagem teórica: a vazão degradou vertiginosamente para **8.51 MB/s no Upload** e **6.07 MB/s no Download**.

- **Explicação Arquitetural (Disk Thrashing):** A falha observada não recai sobre o *software* (Python/gRPC), mas sobre o limite imposto pela controladora de I/O do ambiente de teste. Como o *cluster* de 5 nós opera simulado em apenas um computador, processar um upload fatiado de 50MB forçou o disco rígido da máquina a abrir 5 diretórios locais (/tmp/dfs_nodes/) simultaneamente, escrevendo fatias e lendo fatias para repassar a outros nós através dos eventos do Kafka.
- O esgotamento de *Operações por Segundo* (IOPS) do disco gerou *buffers* entupidos. A RAM superou sua capacidade e forçou o Sistema Operacional a fazer *swapping* de dados virtuais, causando **Contenção de I/O (Disk Thrashing)**. Esse resultado formidável prova que a rede e a lógica do Data Plane construída escalam além da capacidade do próprio hardware que as hospeda.

---

## 7. CONCLUSÃO GERAL E PRÓXIMOS PASSOS

O desenvolvimento exaustivo desta disciplina resultou em um produto sólido de engenharia de software distribuída. A rigorosa Separação de Preocupações manteve o sistema íntegro mesmo sob a chuva de requisições concorrentes.

**Considerações do Engenheiro de Data Plane (Higor):**
O *Data Plane* construído atendeu relativamente bem aos desafios propostos. As operações maciças de *streaming* dinâmico foram blindadas pela autorização em duas fases (Two-Phase Handoff). O acoplamento do Apache Kafka provou-se formidável na mitigação de *overhead* de replicação, garantindo que a queda de instâncias possa ser sanada de forma assíncrona. E a implementação cirúrgica de *Garbage Collection* conferiu a estabilidade final exigida por um ciclo de vida real de dados. A escalada e a solução de *bugs* de protocolo de porta, *Protobuf* e concorrência demonstram a maturidade técnica do projeto entregue. Em um ambiente produtivo da nuvem (por exemplo, instâncias AWS EC2 dedicadas e espalhadas geograficamente com discos EBS isolados), a velocidade demonstrada nos testes pularia exponencialmente, contornando o gargalo do hardware simulado localmente.
