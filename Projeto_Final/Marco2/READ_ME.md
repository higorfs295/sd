# Distributed File System (DFS) — Marco 2

## 📌 Visão Geral

Este projeto implementa um **Sistema de Arquivos Distribuído (DFS)** utilizando sockets TCP e serialização com Protobuf.

No **Marco 2**, o sistema evolui de uma arquitetura centralizada para uma arquitetura distribuída com:

* Múltiplos nós de armazenamento
* Particionamento de dados (sharding)
* Coordenador responsável por roteamento
* Comunicação entre processos independentes

---

## 🧠 Arquitetura

O sistema é composto por três camadas principais:

* **Cliente (CLI)** → envia requisições
* **Coordenador** → decide para qual nó enviar
* **Nós de armazenamento** → executam operações localmente

Fluxo geral:

```
CLI → Coordenador → Nó correto → Storage local
```

---

## ⚙️ Funcionalidades

* `PUT <arquivo>` → armazena um arquivo
* `GET <arquivo>` → recupera um arquivo
* `DELETE <arquivo>` → remove um arquivo
* `LIST` → lista arquivos distribuídos

---

## 🧩 Conceitos Implementados

### 🔹 Sharding

Distribuição dos arquivos baseada em hash:

```
hash(path) % número_de_nós
```

Isso garante que:

* o mesmo arquivo sempre vá para o mesmo nó
* haja uma distribuição inicial simples entre os nós

---

### 🔹 Node ID

Identifica qual nó processou a requisição.

---

### 🔹 Shard ID

Representa a partição lógica associada ao arquivo.

---

## 📂 Estrutura do Projeto

```
DFS/
├── src/dfs/
│   ├── application/
│   ├── cluster/
│   ├── interface/
│   ├── storage/
│   ├── pb/
│   ├── config.py
│   ├── protocol.py
│   └── client.py
│
├── proto/
│   └── dfs.proto
│
├── data/
│   └── nodes/
│       ├── node1/
│       ├── node2/
│       └── node3/
│
├── scripts/
│   ├── start_coordinator.py
│   ├── start_node1.py
│   ├── start_node2.py
│   └── start_node3.py
│
├── requirements.txt
└── README.md
```

---

## 🚀 Como Executar

### 1. Criar ambiente virtual

```bash
python -m venv .venv
```

---

### 2. Ativar ambiente

**Linux/Mac:**

```bash
source .venv/bin/activate
```

**Windows:**

```bash
.venv\Scripts\activate
```

---

### 3. Instalar dependências

```bash
pip install -r requirements.txt
```

---

### 4. Gerar arquivos do Protobuf

Sempre que alterar o arquivo `.proto`:

```bash
python -m grpc_tools.protoc -I=proto --python_out=src proto/dfs.proto
```

---

### 5. Criar diretórios dos nós

```bash
mkdir -p data/nodes/node1
mkdir -p data/nodes/node2
mkdir -p data/nodes/node3
```

---

### 6. Subir os nós (3 terminais)

```bash
python scripts/start_node1.py
```

```bash
python scripts/start_node2.py
```

```bash
python scripts/start_node3.py
```

---

### 7. Subir o coordenador

```bash
python scripts/start_coordinator.py
```

---

### 8. Rodar o cliente (CLI)

```bash
python -m dfs
```

---

## 🧪 Teste rápido

Crie um arquivo:

```bash
echo "teste distribuidos" > teste.txt
```

Envie:

```bash
put teste.txt
```

Liste:

```bash
list
```

Baixe:

```bash
get teste.txt
```

Remova:

```bash
delete teste.txt
```

---

## 🔍 Fluxos

### PUT

```
CLI → Coordenador → Shard → Nó → Disco
```

### GET

```
CLI → Coordenador → Nó correto → Retorno
```

### LIST

```
Coordenador consulta todos os nós → agrega resultados
```

---

## 🧪 Critérios do Marco 2

✔ Múltiplos nós de armazenamento
✔ Distribuição correta dos dados
✔ Balanceamento inicial
✔ Comunicação entre nós via socket

---

## ⚠️ Possíveis Problemas

* Protobuf não gerado
* Porta já em uso
* Diretórios não criados
* Nós não iniciados antes do coordenador

---

## 📌 Próximos Passos

* Replicação de dados
* Tolerância a falhas
* Rebalanceamento dinâmico
* Monitoramento de nós

---

## 👨‍💻 Observações

* O sistema utiliza hashing determinístico
* Não há replicação no Marco 2
* Cada arquivo pertence a um único nó

---

## 👨‍💻 Autor

HIGOR FERREIRA SILVA/202201635
