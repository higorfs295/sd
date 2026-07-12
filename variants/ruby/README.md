# Sistema de Arquivos Distribuído (DFS) — Variante Ruby

Reimplementação em **Ruby** do Sistema de Arquivos Distribuído originalmente
escrito em Python. Preserva a **mesma natureza, arquitetura e requisitos** do
projeto de referência (coordenação centralizada com dados distribuídos, no estilo
GFS/HDFS), adaptando apenas a *stack* de transporte.

> Projeto original: *Sistema de Arquivos Distribuído* — Sistemas Distribuídos 1,
> EMC/UFG (Vitória Mendonça e Higor Ferreira Silva).

---

## 1. O que muda em relação ao original (e o que se mantém)

| Aspecto | Original (Python) | Esta variante (Ruby) |
|---|---|---|
| Arquitetura | Coordenador + nós + CLI, controle/dados separados | **Idêntica** |
| Transporte de controle | gRPC unário | JSON por linha sobre TCP (`lib/protocol.rb`) |
| Transporte de dados | gRPC streaming | JSON por linha sobre TCP; bytes em base64 |
| Mensageria de cura | Apache Kafka (re-replicação assíncrona) | RPC de controle direto (`REPLICATE`) — mesmo papel, sem broker |
| Metadados | JSON em disco | **Idêntico** (JSON em `data/metadata`) |
| Placement | Round-robin determinístico persistido | **Idêntico** (`lib/placement.rb`) |
| Chunking adaptável | `chunking.py` | **Idêntico** (`choose_chunk_size`) |
| Quórum de escrita | 2 de 3 | **Idêntico** |
| Heartbeat 3 estados | ALIVE/SUSPECT/DEAD preguiçoso | **Idêntico** |
| Re-replicação, GC, elasticidade | Sim | **Sim** |

A essência — **separação plano de controle / plano de dados, chunking,
placement determinístico, replicação com quórum, detecção de falhas por
heartbeat, re-replicação automática e consistência eventual** — é integralmente
preservada. Apenas a tecnologia de comunicação foi trocada por uma nativa e sem
dependências externas (só a biblioteca-padrão do Ruby).

---

## 2. Arquitetura

```
            plano de CONTROLE (metadados leves)
   CLI  <───────────────────────────────────►  COORDENADOR
    │        REQUEST_UPLOAD / CONFIRM /            (coordinator.rb)
    │        REQUEST_DOWNLOAD / LIST / DELETE      - registro de nós + vivacidade
    │        + REGISTER / HEARTBEAT (dos nós)      - placement determinístico
    │                                             - metadados (JSON)
    │                                             - supervisor de re-replicação
    │  plano de DADOS (bytes dos arquivos)         - garbage collection
    └──────────────►  NÓS DE ARMAZENAMENTO  ◄─────► NÓS (fan-out entre si)
         STORE / FETCH / DELETE / LIST / REPLICATE   (node.rb)
```

- **Coordenador** (`coordinator.rb`): cérebro do sistema. Nunca toca nos bytes.
- **Nós** (`node.rb`): guardam chunks, replicam entre si com quórum, batem heartbeat.
- **CLI** (`client.rb`): cliente fraco; fala controle com o coordenador e dados com os nós.

---

## 3. Componentes (mapa do código)

| Arquivo | Papel | Equivalente no original |
|---|---|---|
| `config.rb` | Parâmetros centrais (nós, RF, chunk, heartbeat) | `dfs/config.py` |
| `lib/protocol.rb` | Transporte TCP + JSON por linha | (camada gRPC) |
| `lib/placement.rb` | Round-robin determinístico | `cluster/placement.py` |
| `coordinator.rb` | Plano de controle | `server.py` + `node_registry.py` + `metadata_service.py` + `replication_watcher.py` |
| `node.rb` | Plano de dados | `storage_node.py` + `data_service.py` + `local_storage.py` |
| `client.rb` | CLI put/get/list/rm/status | `cli.py` + `client.py` |
| `run_cluster.rb` | Orquestrador do cluster | `run_cluster.py` |

---

## 4. Pré-requisitos

Apenas **Ruby 3.0+** (testado em Ruby 4.0). Nenhuma gem externa — usa apenas
`socket`, `json`, `base64`, `thread` da biblioteca-padrão. Não precisa de Docker,
Kafka ou gRPC.

---

## 5. Como executar

### 5.1. Subir o cluster (coordenador + 5 nós)

```
cd variants/ruby
ruby run_cluster.rb
```

O orquestrador sobe o coordenador (porta 9100) e os cinco nós (portas 9101–9105)
como processos independentes. Deixe a janela aberta; `Ctrl+C` encerra tudo.

### 5.2. Usar a CLI (em outro terminal)

```
ruby client.rb put <arquivo_local> <caminho_dfs>   # enviar
ruby client.rb get <caminho_dfs> <arquivo_local>   # baixar
ruby client.rb list                                 # listar
ruby client.rb rm  <caminho_dfs>                    # remover
ruby client.rb status                               # estado dos nós (ALIVE/SUSPECT/DEAD)
```

Exemplo completo:

```
ruby client.rb put ./foto.jpg /album/foto.jpg
ruby client.rb list
ruby client.rb get /album/foto.jpg ./foto_baixada.jpg
```

---

## 6. Como validar os requisitos

**Correção (integridade byte a byte).** Envie um arquivo, baixe e compare:

```
ruby client.rb put grande.bin /demo/grande.bin
ruby client.rb get /demo/grande.bin saida.bin
# compare grande.bin e saida.bin — devem ser idênticos
```

**Tolerância a falhas + re-replicação.** Com o cluster no ar e um arquivo
enviado, encerre um dos processos de nó (feche a janela ou mate o PID da porta
9101–9105). Em ~10–14 s o coordenador classifica o nó como `DEAD`
(`ruby client.rb status`), a re-replicação restaura o fator 3 em nós vivos, e o
`get` continua devolvendo o arquivo íntegro (disponibilidade preservada).

**Escalabilidade / elasticidade.** Suba um nó extra em runtime — ele se registra
e passa a receber placement nos uploads seguintes, sem reorganizar dados antigos:

```
ruby node.rb node6 9106 ./data/nodes/node6
```

---

## 7. Decisões de projeto preservadas

- **Separação controle/dados**: o coordenador só troca metadados; os bytes fluem
  direto entre CLI e nós. É o que permite escalar horizontalmente.
- **Placement determinístico e persistido**: chunk `i` → nós `i, i+1, i+2 (mod N)`,
  decidido uma vez e gravado nos metadados; nunca recalculado (correção sob mudança
  de cluster).
- **Quórum de escrita (2/3)**: durabilidade garantida no instante do `confirm`.
- **Consistência eventual**: re-replicação restaura réplicas perdidas; a coleta de
  órfãos (via block report no heartbeat) remove cópias redundantes em poucos ciclos.
- **Coordenador único**: modelo GFS/HDFS v1; recuperação relendo o índice persistido.

Para o detalhamento conceitual completo, consulte o `README.md` e o
`ARQUITETURA.md` do projeto original em Python.
