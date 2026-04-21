# DFS_M1 — Sistema de Arquivos Distribuído (Marco 1)

## Visão Geral

Este projeto implementa o Marco 1 de um Sistema de Arquivos Distribuído (DFS), com foco em:

- arquitetura cliente-servidor
- comunicação via sockets TCP
- serialização binária com Protobuf
- armazenamento local em um único nó
- interface de linha de comando (CLI)

## Arquitetura

O sistema é dividido em camadas:

    Cliente (CLI)
        ↓
    Client (socket + protobuf)
        ↓
    Servidor (TCP)
        ↓
    Service (lógica)
        ↓
    Storage (filesystem local)

### Componentes principais

- CLI: interface do usuário
- Client: comunicação com o servidor
- Server: recebe conexões
- Service: processa operações
- Storage: manipula arquivos locais
- Protocol (Protobuf): define mensagens

## Estrutura do Projeto

    DFS_M1/
    ├── src/
    │   └── dfs/
    │       ├── interface/
    │       ├── application/
    │       ├── storage/
    │       ├── pb/
    │       ├── client.py
    │       ├── protocol.py
    │       ├── frame.py
    │       ├── config.py
    │       └── __main__.py
    ├── proto/
    │   └── dfs.proto
    ├── data/
    │   └── storage/
    ├── testes/
    │   ├── entrada/
    │   └── saida/
    └── requirements.txt

## Tecnologias Utilizadas

- Python 3.11+
- Sockets TCP
- Google Protocol Buffers (protobuf)
- CLI com argparse

## Instalação

### 1. Instalar dependências

    python -m pip install protobuf grpcio-tools

### 2. Gerar arquivos Protobuf

    mkdir -p src/dfs/pb

    python -m grpc_tools.protoc \
      -I=proto \
      --python_out=src/dfs/pb \
      proto/dfs.proto

### 3. Garantir pacotes Python

    touch src/dfs/__init__.py
    touch src/dfs/pb/__init__.py

### 4. Configurar PYTHONPATH

    export PYTHONPATH=$(pwd)/src

## Execução

### 1. Iniciar o servidor

    python -m dfs.interface.server

### 2. Em outro terminal, configurar novamente o PYTHONPATH

    export PYTHONPATH=$(pwd)/src

## Uso da CLI

### Enviar arquivo (PUT)

    python -m dfs put testes/entrada/teste.txt docs/teste.txt

### Listar arquivos

    python -m dfs list

### Baixar arquivo (GET)

    python -m dfs get docs/teste.txt testes/saida/teste.txt

### Remover arquivo

    python -m dfs rm docs/teste.txt

## Fluxo de Funcionamento

### Upload (PUT)

1. CLI lê o arquivo local em testes/entrada
2. O cliente serializa a mensagem com Protobuf
3. A mensagem é enviada via socket
4. O servidor recebe a requisição
5. A camada de serviço processa a operação
6. O storage salva o arquivo em data/storage

### Download (GET)

1. O cliente solicita o arquivo
2. O servidor lê o conteúdo do storage
3. A resposta é serializada
4. O cliente recebe a mensagem
5. A CLI salva o arquivo em testes/saida

## Testes Manuais

### 1. Criar arquivo de teste

    echo "hello dfs" > testes/entrada/teste.txt

### 2. Enviar

    python -m dfs put testes/entrada/teste.txt docs/teste.txt

### 3. Baixar

    python -m dfs get docs/teste.txt testes/saida/teste.txt

### 4. Validar

    diff testes/entrada/teste.txt testes/saida/teste.txt

Se não houver saída, os arquivos são idênticos.

## Problemas Comuns

### ModuleNotFoundError

    export PYTHONPATH=$(pwd)/src

### Protobuf não encontrado

    pip install protobuf grpcio-tools

### Arquivo não encontrado

    ls testes/entrada

### Servidor travado no Windows PowerShell

    Stop-Process -Name python -Force

## Decisões de Projeto

- uso de Protobuf para eficiência e padronização
- framing com tamanho para controle de mensagens no socket
- separação entre cliente e servidor
- arquitetura modular para evolução futura

## Próximos Passos

- suporte a múltiplos nós
- replicação de arquivos
- balanceamento de carga
- tolerância a falhas
- interface web

## Conclusão

O projeto estabelece uma base funcional para um sistema distribuído, com comunicação estruturada, organização modular e fluxo claro entre cliente e servidor.