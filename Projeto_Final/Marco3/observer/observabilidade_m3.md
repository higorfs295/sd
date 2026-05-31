# Documentação de Observabilidade do Cluster — DFS Marco 3

Este documento descreve o componente `cluster_observer_m3.py` e seus arquivos auxiliares. O observer atua como uma camada de telemetria passiva: escuta eventos do sistema de arquivos e dos metadados do cluster, persiste logs isolados por desenvolvedor e consolida relatórios de auditoria para entrega.

---

## 1. Contexto e Decisões de Design

### Por que observabilidade passiva?

O cluster do Marco 3 é composto por seis processos independentes (1 coordenador + 5 nós), cada um escrevendo em disco e comunicando-se por gRPC. Sem um observador externo, a única fonte de diagnóstico são os logs de stdout multiplexados pelo `run_cluster.py` — que misturam saídas de todos os processos e não persistem entre sessões.

O observer resolve isso monitorando dois tipos de eventos:

- **Eventos de metadados** — mutações no `metadata_index.json` (PUT, DELETE, incremento de versão)
- **Eventos físicos de disco** — criação e remoção de arquivos `_chunk_N` nos diretórios dos nós

Ele não se comunica com o cluster via gRPC, não interfere no data plane e pode ser iniciado ou parado a qualquer momento sem afetar o sistema.

### Decisões corrigidas em relação à versão anterior

| Problema original | Correção aplicada |
| :--- | :--- |
| Pattern `".chunk_"` não batia com naming real | Regex `_chunk_\d+$` alinhado ao `LocalStorage` |
| `registrar_no_markdown` sem proteção de concorrência | `threading.Lock` global antes de cada escrita |
| Consolidação quebrava em branches com `/` no nome | Branch sanitizado com `replace("/", "-")` |
| `exibir_mapa_distribuicao` ignorava campo `replicas` | Exibe todas as réplicas e sinaliza quórum |
| Docstrings diziam "Sockets TCP" | Corrigido para gRPC (transporte real do M3) |
| Sem health check de portas | `verificar_saude_grpc()` com socket TCP |
| Sem modo snapshot (`--status`) | Flag `--status` implementada |
| Sem rastreamento de deleção física | `on_deleted` adicionado ao handler |
| Sem diff de versão legível | Log exibe "v{anterior} → v{atual}" |
| Portas mapeadas para o Marco 2 (50051–50054) | Corrigido para as portas reais do Marco 3 (9100–9105) |
| Arquivos espalhados na raiz de Marco3/ | Todos os arquivos do observer ficam em `observer/` |

---

## 2. Estrutura de Arquivos

Os arquivos abaixo devem ser criados dentro da pasta `observer/`, que fica na raiz do Marco 3 (ao lado de `run_cluster.py`).

```
Marco3/
├── observer/                         ← pasta do observer (criar esta pasta)
│   ├── cluster_observer_m3.py        ← script principal
│   ├── requirements_observer.txt     ← dependência extra (watchdog)
│   └── logs_auditoria/               ← gerado automaticamente, ignorado pelo git
│       ├── LOGS_main_dev1.md
│       └── LOGS_feature-m3-data-plane_dev2.md
├── RELATORIO_FINAL_MARCO3.md         ← gerado por --consolidar (na raiz), ignorado pelo git
├── run_cluster.py
└── run_cli.py
```

---

## 3. Arquivo: `observer/requirements_observer.txt`

Crie este arquivo dentro de `observer/`. A biblioteca `watchdog` não está no `requirements.txt` principal porque é uma dependência de desenvolvimento, não de produção.

```text
# Dependências do observer de desenvolvimento — não instalar em produção
watchdog>=3.0.0
```

Instalação (com a venv ativa, a partir de `Marco3/`):

```bash
pip install -r observer/requirements_observer.txt
```

No Windows, se os códigos de cor ANSI não aparecerem no terminal, execute uma vez antes de iniciar o observer:

```bash
# Habilitar ANSI no Windows Terminal / CMD
reg add HKCU\Console /v VirtualTerminalLevel /t REG_DWORD /d 1 /f
```

---

## 4. Arquivo: `.gitignore` — adições necessárias

Adicione estes blocos ao `.gitignore` na raiz do repositório (não do Marco 3):

```gitignore
# ─── Telemetria do observer — logs isolados por dev ───────────────────────────
observer/logs_auditoria/
RELATORIO_FINAL_MARCO3.md

# ─── Estado mutável do cluster (não versionar dados de runtime) ───────────────
DFS_M3/data/nodes/node*/
DFS_M3/data/metadata/metadata_index.json

# ─── Stubs gerados pelo compilador protoc (regenerar localmente) ──────────────
DFS_M3/dfs/pb/dfs_pb2.py
DFS_M3/dfs/pb/dfs_pb2_grpc.py
```

> **Atenção:** `dfs_pb2.py` e `dfs_pb2_grpc.py` são gerados pelo `protoc` e diferem em conteúdo entre sistemas operacionais (caminhos absolutos embarcados nos stubs). Versioná-los causa conflitos de merge garantidos.

---

## 5. Arquivo principal: `observer/cluster_observer_m3.py`

```python
# =============================================================================
# ARQUIVO: cluster_observer_m3.py
# LOCALIZAÇÃO: Marco3/observer/
# =============================================================================
#
# DESCRIÇÃO GERAL:
#   Observer passivo do cluster DFS — Marco 3.
#   Monitora eventos de sistema de arquivos (watchdog) para rastrear:
#     - Mutações no metadata_index.json (PUT, DELETE, versionamento)
#     - Criação e deleção física de arquivos _chunk_N nos nós
#     - Alterações em arquivos .py e .proto durante desenvolvimento
#
#   Persiste logs em Markdown isolados por desenvolvedor (branch + usuário).
#   Não se comunica com o cluster via gRPC — é 100% passivo e não-invasivo.
#
# MODOS DE EXECUÇÃO (a partir de Marco3/):
#   python observer/cluster_observer_m3.py             → inicia o loop de monitoramento
#   python observer/cluster_observer_m3.py --status    → snapshot único do cluster e sai
#   python observer/cluster_observer_m3.py --consolidar → une logs de todos os devs e sai
#
# DEPENDÊNCIAS:
#   pip install -r observer/requirements_observer.txt
#   (demais imports são da stdlib Python)
#
# =============================================================================

import sys
import time
import os
import re
import json
import socket
import threading
import subprocess
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# =============================================================================
# SEÇÃO 1: CONSTANTES DE CONFIGURAÇÃO
# =============================================================================
#
# Caminhos derivados do layout do Marco 3. O observer é executado a partir
# de Marco3/, então todos os caminhos são relativos a essa raiz.

# Caminho base do pacote dentro do Marco 3
BASE_PATH = "DFS_M3"

# Caminho físico do banco de metadados central do coordenador
INDEX_PATH = os.path.join(BASE_PATH, "data", "metadata", "metadata_index.json")

# Diretório raiz dos nós de armazenamento
NODES_DIR = os.path.join(BASE_PATH, "data", "nodes")

# Mapeamento estático de componente → porta gRPC (espelha config.py do DFS)
# Usado pelo health check para verificar se cada processo está online
NOS_GRPC = {
    "coordinator": 9100,
    "node1":       9101,
    "node2":       9102,
    "node3":       9103,
    "node4":       9104,
    "node5":       9105,
}

# Fator de replicação esperado — alerta se algum chunk tiver menos réplicas
FATOR_REPLICACAO_ESPERADO = 3

# Pasta de saída dos logs de auditoria por desenvolvedor.
# Fica dentro de observer/ para não poluir a raiz do projeto.
PASTA_AUDITORIA = os.path.join("observer", "logs_auditoria")

# Arquivo de relatório consolidado gerado na raiz de Marco3/
ARQUIVO_RELATORIO = "RELATORIO_FINAL_MARCO3.md"

# Regex que identifica arquivos de chunk no disco dos nós.
# O LocalStorage salva chunks com o padrão: {caminho_normalizado}_chunk_{N}
# Exemplos reais: "docs_relatorio.pdf_chunk_0", "videos_aula_chunk_2"
REGEX_CHUNK = re.compile(r"_chunk_\d+$")

# Conjunto de prefixos de caminho a ignorar nos eventos do watchdog.
# Evita loops de feedback ao monitorar a própria pasta de logs.
IGNORAR_CAMINHOS = {".venv", "__pycache__", ".git", "logs_auditoria", "dfs_pb2"}


# =============================================================================
# SEÇÃO 2: CORES DO TERMINAL (ANSI)
# =============================================================================

class Cores:
    HEADER  = '\033[95m'   # magenta  — cabeçalhos de seção
    INFO    = '\033[94m'   # azul     — eventos informativos
    SUCCESS = '\033[92m'   # verde    — operações bem-sucedidas (PUT, WRITE)
    WARNING = '\033[93m'   # amarelo  — alertas (fallback, quórum incompleto)
    FAIL    = '\033[91m'   # vermelho — erros e deleções
    RESET   = '\033[0m'    # reset    — sempre ao final de qualquer print colorido
    BOLD    = '\033[1m'    # negrito  — nomes de arquivo e valores importantes
    CYAN    = '\033[96m'   # ciano    — topologia e health check


# =============================================================================
# SEÇÃO 3: IDENTIFICAÇÃO DO DESENVOLVEDOR (GIT)
# =============================================================================

def obter_info_git() -> tuple[str, str]:
    """
    Obtém o nome da branch ativa e o usuário corrente para isolar os logs.

    A branch é sanitizada (substituindo "/" por "-") para evitar que nomes
    como "feature/m3-data-plane" quebrem o parsing do nome do arquivo de log
    na consolidação. O usuário é resolvido a partir do ambiente do SO como
    fallback caso os comandos git não estejam disponíveis.

    Returns:
        tuple[str, str]: (branch_sanitizada, nome_do_usuario)
    """
    try:
        branch_raw = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
        # Sanitiza barras para não quebrar o nome do arquivo em Windows/Linux
        branch = branch_raw.replace("/", "-").replace("\\", "-")
    except Exception:
        branch = "sem-branch"

    # Tenta primeiro via variável de ambiente (mais portátil que os.getlogin())
    usuario = (
        os.environ.get("USERNAME")         # Windows
        or os.environ.get("USER")          # Linux/macOS
        or os.environ.get("LOGNAME")       # POSIX fallback
    )
    if not usuario:
        try:
            usuario = os.getlogin()
        except Exception:
            usuario = "dev_anonimo"

    return branch, usuario


# Resolvidos uma vez no import — usados como constantes no restante do módulo
BRANCH_ATUAL, USUARIO_ATUAL = obter_info_git()
NOME_ARQUIVO_LOG = f"LOGS_{BRANCH_ATUAL}_{USUARIO_ATUAL}.md"


# =============================================================================
# SEÇÃO 4: PERSISTÊNCIA DE LOGS EM MARKDOWN
# =============================================================================

# Lock global que serializa escritas concorrentes no arquivo de log.
# Sem este lock, eventos simultâneos (ex: 3 chunks gravados em paralelo)
# podem corromper o arquivo com escrita intercalada de linhas.
_lock_escrita_log = threading.Lock()


def inicializar_arquivos_documentacao() -> None:
    """
    Cria a infraestrutura de logs locais caso ainda não exista.

    Cria a pasta observer/logs_auditoria/ e o arquivo Markdown individual do
    desenvolvedor com cabeçalho, metadados da sessão e header da tabela.
    Operação idempotente — seguro chamar múltiplas vezes.
    """
    os.makedirs(PASTA_AUDITORIA, exist_ok=True)

    caminho_log = os.path.join(PASTA_AUDITORIA, NOME_ARQUIVO_LOG)
    if not os.path.exists(caminho_log):
        with open(caminho_log, "w", encoding="utf-8") as f:
            f.write("# Diário de Bordo — Cluster DFS Marco 3\n\n")
            f.write(f"- **Branch:** `{BRANCH_ATUAL}`\n")
            f.write(f"- **Operador:** `{USUARIO_ATUAL}`\n")
            f.write(f"- **Sessão iniciada:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n\n")
            f.write("| Timestamp | Componente | Evento | Detalhes |\n")
            f.write("| :--- | :--- | :--- | :--- |\n")


def _sanitizar_ansi(texto: str) -> str:
    """
    Remove todos os códigos de escape ANSI de uma string.

    Os códigos de cor são úteis no terminal mas poluem o Markdown se persistidos
    diretamente. Esta função usa regex para cobrir qualquer sequência ANSI.
    """
    return re.sub(r'\033\[[0-9;]*m', '', texto)


def registrar_no_markdown(componente: str, evento: str, detalhes: str) -> None:
    """
    Persiste uma linha de evento na tabela Markdown do desenvolvedor.

    Thread-safe: usa _lock_escrita_log para serializar acessos concorrentes.
    Sanitiza códigos ANSI antes de escrever para garantir Markdown válido.

    Args:
        componente: nome do subsistema que gerou o evento (ex: "NODE1", "COORDENADOR")
        evento:     código do tipo de evento (ex: "PUT_COMMIT", "REPLICA_GRAVADA")
        detalhes:   descrição livre do evento, pode conter formatação ANSI
    """
    timestamp   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg_limpa   = _sanitizar_ansi(detalhes)
    caminho_log = os.path.join(PASTA_AUDITORIA, NOME_ARQUIVO_LOG)

    with _lock_escrita_log:
        try:
            with open(caminho_log, "a", encoding="utf-8") as f:
                f.write(f"| {timestamp} | `{componente}` | **{evento}** | {msg_limpa} |\n")
        except Exception as exc:
            # Não levanta exceção — o observer nunca deve travar o terminal
            print(f"[OBSERVER ERRO] Falha ao persistir log: {exc}")


def log_evento(nivel: str, componente: str, evento: str, mensagem: str) -> None:
    """
    Imprime evento no terminal (com cor) e persiste no Markdown (sem cor).

    Args:
        nivel:      código de cor ANSI para a linha no terminal
        componente: subsistema de origem
        evento:     código do tipo de evento
        mensagem:   descrição completa, pode incluir códigos ANSI para destaque
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {nivel}[{componente} -> {evento}]{Cores.RESET} {mensagem}{Cores.RESET}")
    registrar_no_markdown(componente, evento, mensagem)


# =============================================================================
# SEÇÃO 5: HEALTH CHECK DAS PORTAS GRPC
# =============================================================================

def verificar_saude_grpc() -> dict[str, bool]:
    """
    Verifica se cada processo do cluster está aceitando conexões TCP.

    Realiza um connect TCP simples na porta de cada componente. Não envia
    nenhuma mensagem gRPC — apenas testa se o socket está aberto (ou seja,
    se o processo está vivo). Timeout de 0.3s por porta para resposta rápida.

    Returns:
        dict[str, bool]: mapeamento componente → True (online) | False (offline)
    """
    resultado = {}
    for nome, porta in NOS_GRPC.items():
        try:
            with socket.create_connection(("127.0.0.1", porta), timeout=0.3):
                resultado[nome] = True
        except (ConnectionRefusedError, TimeoutError, OSError):
            resultado[nome] = False
    return resultado


def exibir_health_check() -> None:
    """
    Imprime no terminal o status de saúde de cada componente do cluster.

    Chama verificar_saude_grpc() e formata o resultado com cores.
    Usado tanto no startup do observer quanto no modo --status.
    """
    print(f"\n{Cores.CYAN}{'─' * 55}")
    print(f"  HEALTH CHECK — PROCESSOS GRPC (porta TCP)")
    print(f"{'─' * 55}{Cores.RESET}")

    saude = verificar_saude_grpc()
    todos_ok = all(saude.values())

    for nome, online in saude.items():
        porta  = NOS_GRPC[nome]
        status = f"{Cores.SUCCESS}ONLINE{Cores.RESET} " if online else f"{Cores.FAIL}OFFLINE{Cores.RESET}"
        print(f"  {nome:<15} :{porta}   {status}")

    if not todos_ok:
        offline = [n for n, ok in saude.items() if not ok]
        print(f"\n  {Cores.WARNING}Aviso: {len(offline)} componente(s) offline: {', '.join(offline)}")
        print(f"  Rode 'python run_cluster.py' para subir o cluster.{Cores.RESET}")

    print(f"{Cores.CYAN}{'─' * 55}{Cores.RESET}\n")


# =============================================================================
# SEÇÃO 6: MAPA DE DISTRIBUIÇÃO DO CLUSTER
# =============================================================================

def _avaliar_quorum_chunk(chunk: dict) -> tuple[int, bool]:
    """
    Avalia quantas réplicas um chunk possui e se o quórum de escrita foi atingido.

    O Marco 3 usa R=3 e W=2 (quórum de escrita): o PUT só é considerado
    bem-sucedido se pelo menos 2 das 3 réplicas foram gravadas.

    Args:
        chunk: dicionário de metadado do chunk (do metadata_index.json)

    Returns:
        tuple[int, bool]: (numero_de_replicas, quorum_atingido)
    """
    replicas = chunk.get("replicas", [])

    # Compatibilidade com o formato legado (sem lista de réplicas explícita)
    if not replicas and chunk.get("node_id"):
        return 1, False  # 1 réplica conhecida → abaixo do quórum W=2

    n_replicas = len(replicas)
    quorum_ok  = n_replicas >= 2  # W=2: mínimo aceitável
    return n_replicas, quorum_ok


def exibir_mapa_distribuicao(index: dict) -> None:
    """
    Imprime a topologia atual do cluster a partir do metadata_index.json.

    Exibe todas as réplicas de cada chunk (campo "replicas"), avalia o status
    de quórum por chunk e contabiliza volumetria por nó.

    Args:
        index: dicionário deserializado do metadata_index.json
    """
    if not index:
        print(f"\n{Cores.CYAN}[MAPA M3] Catálogo vazio — nenhum arquivo no DFS.{Cores.RESET}\n")
        return

    print(f"\n{Cores.CYAN}{'═' * 70}")
    print(f"  TOPOLOGIA ATIVA DO CLUSTER (MARCO 3)")
    print(f"{'═' * 70}{Cores.RESET}")

    contagem_nos: dict[str, int] = {}
    alertas_quorum: list[str] = []

    for arq_logico, info in index.items():
        versao  = info.get("version", 1)
        chunks  = info.get("chunks", [])
        tamanho = info.get("size", 0)

        if tamanho >= 1024 * 1024:
            tamanho_str = f"{tamanho / (1024*1024):.1f} MB"
        elif tamanho >= 1024:
            tamanho_str = f"{tamanho / 1024:.1f} KB"
        else:
            tamanho_str = f"{tamanho} B"

        print(f"\n  {Cores.BOLD}{arq_logico}{Cores.RESET}")
        print(f"  Versao v{versao} | {len(chunks)} chunk(s) | {tamanho_str}")

        for i, chunk in enumerate(chunks):
            n_replicas, quorum_ok = _avaliar_quorum_chunk(chunk)
            replicas = chunk.get("replicas", [])

            if replicas:
                nos_str_parts = []
                for j, rep in enumerate(replicas):
                    node_id = rep.get("node_id", "?")
                    contagem_nos[node_id] = contagem_nos.get(node_id, 0) + 1
                    marca = "(P)" if j == 0 else f"(R{j})"
                    nos_str_parts.append(f"{node_id}{marca}")
                nos_str = " → ".join(nos_str_parts)
            else:
                node_id = chunk.get("node_id", "unknown")
                contagem_nos[node_id] = contagem_nos.get(node_id, 0) + 1
                nos_str = f"{node_id}(P) [sem lista de replicas]"

            if quorum_ok:
                quorum_str = f"{Cores.SUCCESS}W={n_replicas}/{FATOR_REPLICACAO_ESPERADO} ok{Cores.RESET}"
            else:
                quorum_str = f"{Cores.WARNING}W={n_replicas}/{FATOR_REPLICACAO_ESPERADO} ABAIXO DO QUORUM{Cores.RESET}"
                alertas_quorum.append(f"chunk_{i} de '{arq_logico}'")

            print(f"    chunk_{i}: {nos_str}   {quorum_str}")

    print(f"\n  Volumetria por no fisico (contando replicas):")
    for no, qtd in sorted(contagem_nos.items()):
        barra = "█" * min(qtd, 40)
        print(f"    {no:<10} {qtd:>3} bloco(s)  {Cores.INFO}{barra}{Cores.RESET}")

    if alertas_quorum:
        print(f"\n  {Cores.WARNING}Alertas de quorum ({len(alertas_quorum)} chunk(s)):")
        for alerta in alertas_quorum:
            print(f"    - {alerta}")
        print(Cores.RESET, end="")

    print(f"\n{Cores.CYAN}{'═' * 70}{Cores.RESET}\n")


# =============================================================================
# SEÇÃO 7: CONSOLIDAÇÃO DE LOGS DA EQUIPE
# =============================================================================

def consolidar_relatorio_final() -> None:
    """
    Lê todos os logs individuais de observer/logs_auditoria/ e gera um relatório
    unificado e ordenado cronologicamente em RELATORIO_FINAL_MARCO3.md (na raiz).

    Uso: python observer/cluster_observer_m3.py --consolidar
    """
    print(f"{Cores.HEADER}{'═' * 55}")
    print(f"  CONSOLIDANDO LOGS DO MARCO 3")
    print(f"{'═' * 55}{Cores.RESET}\n")

    if not os.path.exists(PASTA_AUDITORIA):
        print(f"{Cores.FAIL}Erro: pasta '{PASTA_AUDITORIA}' nao encontrada.")
        print(f"Rode o observer ao menos uma vez antes de consolidar.{Cores.RESET}")
        return

    todas_linhas: list[str] = []
    contagem_por_dev: dict[str, int] = {}

    for arquivo in sorted(os.listdir(PASTA_AUDITORIA)):
        if not (arquivo.startswith("LOGS_") and arquivo.endswith(".md")):
            continue

        base   = arquivo[len("LOGS_"):-len(".md")]
        partes = base.rsplit("_", 1)
        origem = f"{partes[0]} / {partes[1]}" if len(partes) == 2 else base

        n_eventos = 0
        caminho = os.path.join(PASTA_AUDITORIA, arquivo)
        with open(caminho, "r", encoding="utf-8") as f:
            for linha in f:
                if linha.startswith("| ") and "Timestamp" not in linha and ":---" not in linha:
                    fatias = linha.split("|")
                    fatias.insert(2, f" `{origem}` ")
                    todas_linhas.append("|".join(fatias))
                    n_eventos += 1

        contagem_por_dev[origem] = n_eventos
        print(f"  {arquivo:<45} {n_eventos} eventos lidos")

    if not todas_linhas:
        print(f"\n{Cores.WARNING}Nenhum evento encontrado nos logs. Rode o observer e use o DFS antes de consolidar.{Cores.RESET}")
        return

    todas_linhas.sort(key=lambda x: x.split("|")[1].strip())

    with open(ARQUIVO_RELATORIO, "w", encoding="utf-8") as out:
        out.write("# Relatorio Consolidado de Auditoria — DFS Marco 3\n\n")
        out.write(f"Gerado em: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n\n")
        out.write("## Sumario por Desenvolvedor\n\n")
        out.write("| Desenvolvedor / Branch | Eventos registrados |\n")
        out.write("| :--- | :--- |\n")
        for dev, n in sorted(contagem_por_dev.items()):
            out.write(f"| `{dev}` | {n} |\n")
        out.write("\n## Linha do Tempo Completa\n\n")
        out.write("| Timestamp | Origem | Componente | Evento | Detalhes |\n")
        out.write("| :--- | :--- | :--- | :--- | :--- |\n")
        out.writelines(todas_linhas)

    total = sum(contagem_por_dev.values())
    print(f"\n{Cores.SUCCESS}Relatorio '{ARQUIVO_RELATORIO}' gerado com {total} eventos de {len(contagem_por_dev)} desenvolvedor(es).{Cores.RESET}\n")


# =============================================================================
# SEÇÃO 8: HANDLER DE EVENTOS DO WATCHDOG
# =============================================================================

def _deve_ignorar(caminho: str) -> bool:
    """
    Retorna True se o caminho deve ser ignorado pelo observer.

    Filtra pastas de ambiente virtual, cache Python, git, logs do próprio
    observer e stubs gerados pelo protoc.

    Args:
        caminho: caminho completo do evento do watchdog
    """
    return any(ignorar in caminho for ignorar in IGNORAR_CAMINHOS)


class ClusterObserverM3Handler(FileSystemEventHandler):
    """
    Handler principal do observer. Processa eventos do watchdog e os traduz
    em log de auditoria com semântica de negócio do DFS.

    Monitora três categorias de eventos:
      1. Mutações em metadata_index.json → operações lógicas do cluster (PUT, RM, versão)
      2. Criação/deleção de arquivos _chunk_N → operações físicas nos nós via gRPC
      3. Modificações em .py e .proto → rastreamento de desenvolvimento
    """

    def __init__(self):
        super().__init__()
        self.ultima_versao_index: dict = self._carregar_index() or {}

    def _carregar_index(self) -> dict | None:
        """
        Lê o metadata_index.json com retry para tolerar I/O concorrente.

        Returns:
            dict com o conteúdo do índice, {} se vazio, None se falhou após retries
        """
        if not os.path.exists(INDEX_PATH):
            return {}

        for _ in range(3):
            try:
                with open(INDEX_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, PermissionError):
                time.sleep(0.1)

        return None

    def on_modified(self, event):
        """
        Trata modificações em arquivos monitorados.

        Casos tratados:
          - metadata_index.json: calcula diff para detectar PUT, DELETE ou
            incremento de versão. Exibe mapa de distribuição após cada mutação.
          - Arquivos .py / .proto: registra modificação de código-fonte.
        """
        if _deve_ignorar(event.src_path) or event.is_directory:
            return

        nome = os.path.basename(event.src_path)

        if event.src_path.endswith("metadata_index.json"):
            index_atual = self._carregar_index()
            if index_atual is None:
                return

            if index_atual == self.ultima_versao_index:
                return

            chaves_atuais  = set(index_atual.keys())
            chaves_antigas = set(self.ultima_versao_index.keys())

            novos = chaves_atuais - chaves_antigas
            for novo in novos:
                info     = index_atual[novo]
                n_chunks = len(info.get("chunks", []))
                versao   = info.get("version", 1)
                tamanho  = info.get("size", 0)
                log_evento(
                    Cores.SUCCESS, "COORDENADOR", "PUT_COMMIT",
                    f"{Cores.BOLD}{novo}{Cores.RESET} | "
                    f"v{versao} | {n_chunks} chunk(s) | {tamanho} bytes"
                )

            removidos = chaves_antigas - chaves_atuais
            for removido in removidos:
                log_evento(
                    Cores.FAIL, "COORDENADOR", "DELETE_COMMIT",
                    f"{Cores.BOLD}{removido}{Cores.RESET} removido do catalogo e dos nos"
                )

            if not novos and not removidos:
                for k in chaves_atuais & chaves_antigas:
                    v_antes  = self.ultima_versao_index[k].get("version", 1)
                    v_depois = index_atual[k].get("version", 1)
                    if v_antes != v_depois:
                        log_evento(
                            Cores.INFO, "COORDENADOR", "VERSAO_INCREMENTADA",
                            f"{Cores.BOLD}{k}{Cores.RESET} | "
                            f"v{v_antes} {Cores.CYAN}→{Cores.RESET} v{v_depois}"
                        )

            self.ultima_versao_index = index_atual
            exibir_mapa_distribuicao(index_atual)

        elif nome.endswith((".py", ".proto")):
            if nome in {"cluster_observer_m3.py"}:
                return
            log_evento(
                Cores.WARNING, "REPOSITORIO", "SOURCE_MODIFICADO",
                f"{Cores.BOLD}{nome}{Cores.RESET} — salvo em disco"
            )

    def on_created(self, event):
        """
        Trata criação de novos arquivos.

        Detecta chunks físicos sendo gravados nos diretórios dos nós.
        O padrão regex '_chunk_\\d+$' é o naming real do LocalStorage.
        """
        if _deve_ignorar(event.src_path) or event.is_directory:
            return

        nome = os.path.basename(event.src_path)

        if REGEX_CHUNK.search(nome):
            partes = event.src_path.replace("\\", "/").split("/")
            no_alvo = next(
                (parte for parte in partes if parte.startswith("node")),
                "no-desconhecido"
            )

            match_replica = re.search(r"_chunk_(\d+)$", nome)
            chunk_id = match_replica.group(1) if match_replica else "?"

            log_evento(
                Cores.INFO, no_alvo.upper(), "CHUNK_ESCRITO",
                f"chunk_{chunk_id}: {Cores.BOLD}{nome}{Cores.RESET} "
                f"persistido no disco do {no_alvo} via gRPC WriteChunk"
            )

    def on_deleted(self, event):
        """
        Trata deleção de arquivos físicos.

        Registra a remoção de chunks do disco dos nós, confirmando que
        todas as réplicas foram removidas após um DELETE.
        """
        if _deve_ignorar(event.src_path) or event.is_directory:
            return

        nome = os.path.basename(event.src_path)

        if REGEX_CHUNK.search(nome):
            partes  = event.src_path.replace("\\", "/").split("/")
            no_alvo = next(
                (parte for parte in partes if parte.startswith("node")),
                "no-desconhecido"
            )
            log_evento(
                Cores.FAIL, no_alvo.upper(), "CHUNK_REMOVIDO",
                f"{Cores.BOLD}{nome}{Cores.RESET} deletado do disco do {no_alvo}"
            )


# =============================================================================
# SEÇÃO 9: PONTO DE ENTRADA
# =============================================================================

def _exibir_ajuda() -> None:
    print(f"""
{Cores.BOLD}cluster_observer_m3.py — Observer de auditoria do cluster DFS (Marco 3){Cores.RESET}

Uso (a partir de Marco3/):
  python observer/cluster_observer_m3.py               Inicia o loop de monitoramento
  python observer/cluster_observer_m3.py --status      Snapshot do cluster e sai (sem loop)
  python observer/cluster_observer_m3.py --consolidar  Une logs da equipe em RELATORIO_FINAL_MARCO3.md
  python observer/cluster_observer_m3.py --ajuda       Exibe esta mensagem

Arquivos gerados:
  observer/logs_auditoria/LOGS_{{branch}}_{{usuario}}.md   log isolado por desenvolvedor
  RELATORIO_FINAL_MARCO3.md                              relatório consolidado da equipe (na raiz)
""")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if arg in ("--ajuda", "--help", "-h"):
        _exibir_ajuda()
        sys.exit(0)

    if arg == "--consolidar":
        consolidar_relatorio_final()
        sys.exit(0)

    if arg == "--status":
        print(f"\n{Cores.HEADER}=== SNAPSHOT DO CLUSTER (MARCO 3) ==={Cores.RESET}")
        print(f"Branch: {Cores.CYAN}{BRANCH_ATUAL}{Cores.RESET} | Operador: {Cores.CYAN}{USUARIO_ATUAL}{Cores.RESET}")
        exibir_health_check()
        index = ClusterObserverM3Handler()._carregar_index() or {}
        exibir_mapa_distribuicao(index)
        sys.exit(0)

    # ── Modo padrão: loop de monitoramento ────────────────────────────────────
    inicializar_arquivos_documentacao()

    print(f"\n{Cores.HEADER}{'═' * 55}")
    print(f"  OBSERVER DE AUDITORIA ATIVO (MARCO 3)")
    print(f"{'═' * 55}{Cores.RESET}")
    print(f"  Branch:   {Cores.CYAN}{BRANCH_ATUAL}{Cores.RESET}")
    print(f"  Operador: {Cores.CYAN}{USUARIO_ATUAL}{Cores.RESET}")
    print(f"  Log:      {Cores.BOLD}{PASTA_AUDITORIA}/{NOME_ARQUIVO_LOG}{Cores.RESET}")
    print(f"  Ctrl+C para encerrar\n")

    exibir_health_check()

    index_inicial = ClusterObserverM3Handler()._carregar_index() or {}
    exibir_mapa_distribuicao(index_inicial)
    log_evento(Cores.HEADER, "OBSERVER", "SESSAO_INICIADA",
               f"Monitoramento ativo | branch={BRANCH_ATUAL} | operador={USUARIO_ATUAL}")

    handler  = ClusterObserverM3Handler()
    observer = Observer()
    observer.schedule(handler, path=".", recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        log_evento(Cores.WARNING, "OBSERVER", "SESSAO_ENCERRADA",
                   "Monitoramento encerrado pelo operador via Ctrl+C")
        print(f"\n{Cores.WARNING}Observer encerrado. Log salvo em {PASTA_AUDITORIA}/{NOME_ARQUIVO_LOG}{Cores.RESET}\n")

    observer.join()
```

---

## 6. Roteiro de Uso

### Pré-requisitos

```bash
# A partir da raiz de Marco3/ com a venv ativa:
pip install -r observer/requirements_observer.txt
```

### Fluxo diário de desenvolvimento

**Terminal 1 — cluster:**
```bash
python run_cluster.py
```

**Terminal 2 — observer:**
```bash
python observer/cluster_observer_m3.py
```

O observer exibe o health check das portas gRPC e a topologia atual ao iniciar. A partir daí, todos os eventos são impressos em tempo real e persistidos no log Markdown em `observer/logs_auditoria/`.

**Terminal 3 — CLI (testes normais):**
```bash
python run_cli.py put meu_arquivo.txt dados/meu_arquivo.txt
python run_cli.py get dados/meu_arquivo.txt saida.txt
python run_cli.py list
python run_cli.py rm dados/meu_arquivo.txt
```

### Verificar estado do cluster sem iniciar o loop

```bash
python observer/cluster_observer_m3.py --status
```

Exibe o health check e o mapa de distribuição e encerra. Útil para uma verificação rápida antes de uma demo ou reunião.

### Gerar relatório de entrega

Ao final do desenvolvimento, com os logs dos dois desenvolvedores disponíveis em `observer/logs_auditoria/`:

```bash
python observer/cluster_observer_m3.py --consolidar
```

Gera `RELATORIO_FINAL_MARCO3.md` na raiz de `Marco3/` com:
- Sumário de eventos por desenvolvedor
- Linha do tempo completa ordenada cronologicamente

---

## 7. Catálogo de Eventos Registrados

| Evento | Componente | Quando ocorre |
| :--- | :--- | :--- |
| `PUT_COMMIT` | `COORDENADOR` | Novo arquivo registrado no `metadata_index.json` |
| `DELETE_COMMIT` | `COORDENADOR` | Arquivo removido do índice central |
| `VERSAO_INCREMENTADA` | `COORDENADOR` | Campo `version` de um arquivo foi incrementado |
| `CHUNK_ESCRITO` | `NODE{N}` | Arquivo `_chunk_N` criado fisicamente no disco do nó |
| `CHUNK_REMOVIDO` | `NODE{N}` | Arquivo `_chunk_N` deletado do disco do nó |
| `SOURCE_MODIFICADO` | `REPOSITORIO` | Arquivo `.py` ou `.proto` salvo em disco |
| `SESSAO_INICIADA` | `OBSERVER` | Observer iniciado com sucesso |
| `SESSAO_ENCERRADA` | `OBSERVER` | Observer encerrado via Ctrl+C |

---

## 8. Resolução de Problemas

**O observer não detecta os chunks sendo gravados:**
Confirme o naming real dos chunks no disco com `ls DFS_M3/data/nodes/node1/`. O padrão esperado é `{nome}_chunk_{N}` sem extensão. Se o `LocalStorage` usar outro padrão, ajuste a constante `REGEX_CHUNK`.

**Cores não aparecem no Windows:**
Execute o comando `reg add` da Seção 3 para habilitar ANSI no terminal Windows, ou use o Windows Terminal em vez do CMD clássico.

**`watchdog.observers` não encontrado:**
```bash
pip install -r observer/requirements_observer.txt
```

**O health check mostra todos os nós como OFFLINE:**
O cluster não está rodando. Inicie com `python run_cluster.py` antes de usar o observer.

**Log corrompido com linhas quebradas:**
Indica que dois eventos foram registrados simultaneamente sem o lock. Verifique se está usando a versão corrigida — o `_lock_escrita_log` garante serialização.