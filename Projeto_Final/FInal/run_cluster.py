"""
SISTEMA DE ARQUIVOS DISTRIBUÍDO (DFS) - ORQUESTRADOR CENTRAL DO CLUSTER
=======================================================================
Descrição Geral:
    Este script atua como o Control Plane e gerenciador de ciclo de vida do 
    ecossistema DFS. Ele automatiza a inicialização da infraestrutura de 
    mensageria (Zookeeper e Kafka via Docker), o nó Coordenador gRPC e todos 
    os nós de armazenamento (Data Planes/Storage Nodes) declarados no sistema.

Ordem de Inicialização Crítica:
    1. Infraestrutura Docker (Porta 9092) -> Necessária para o Kafka.
    2. Coordenador gRPC (Porta 9100)      -> Precisa estar online para receber registros.
    3. Storage Nodes (Portas 9101-9105)   -> Conectam-se ao gRPC e assinam tópicos Kafka.

Encerramento:
    Ao capturar um sinal de interrupção (Ctrl+C), o orquestrador garante a 
    derrubada limpa de todos os subprocessos Python e encerra os containers 
    Docker, evitando vazamento de memória ou portas presas no sistema operacional.
"""

from __future__ import annotations

import os
import sys
import time
import socket
import subprocess
from pathlib import Path

# --- DEFINIÇÃO DE DIRETÓRIOS E PATHS ---
# Localiza a raiz do projeto e a pasta interna contendo os pacotes do DFS
ROOT_DIR = Path(__file__).resolve().parent
DFS_DIR = ROOT_DIR / "DFS"

# Injeta o diretório do DFS no sys.path para garantir que o interpretador
# Python consiga localizar o pacote 'dfs' e suas configurações globais.
sys.path.insert(0, str(DFS_DIR))

from dfs.config import NODE_ORDER  # type: ignore[import-not-found]

# --- MAPEAMENTO DE PORTAS PADRÃO DO PROJETO ---
PORTA_COORDENADOR = 9100
PORTAS_NOS = {
    "node1": 9101,
    "node2": 9102,
    "node3": 9103,
    "node4": 9104,
    "node5": 9105
}


def build_env() -> dict[str, str]:
    """
    Monta e estende as variáveis de ambiente para os subprocessos.

    Explicação de utilidade:
        Garante que a variável PYTHONPATH inclua o caminho absoluto para o 
        diretório do DFS. Sem isso, quando o script executar 'storage_node.py' 
        ou 'server.py' em um processo separado, o interpretador Python disparará 
        um erro de 'ModuleNotFoundError'.

    Returns:
        dict[str, str]: Uma cópia do ambiente do Sistema Operacional atualizado.
    """
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")

    paths = [str(DFS_DIR)]
    if current_pythonpath:
        paths.append(current_pythonpath)

    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def is_port_in_use(port: int) -> bool:
    """
    Verifica se uma porta TCP específica já está ocupada no localhost.

    Explicação de utilidade:
        Evita o erro clássico 'Address already in use' no Windows. Se o usuário 
        fechar o cluster incorretamente, processos Python podem ficar rodando em 
        background. Esta função detecta o conflito antes do boot começar.

    Args:
        port (int): O número da porta TCP a ser testada (ex: 9100).

    Returns:
        bool: True se a porta estiver ocupada, False se estiver livre.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for_port(port: int, timeout: float = 15.0) -> bool:
    """
    Aguarda dinamicamente até que uma porta TCP local seja aberta e aceite conexões.

    Explicação de utilidade:
        Substitui os 'time.sleep()' estáticos e cegos. Em vez de esperar 3 segundos 
        fixos pelo Kafka, esta função checa a porta a cada 100ms. Se o Kafka subir 
        em 1 segundo, o cluster continua imediatamente, otimizando o tempo de teste.

    Args:
        port (int): A porta TCP que estamos esperando abrir (ex: 9092 ou 9100).
        timeout (float): Tempo máximo em segundos de espera antes de desistir.

    Returns:
        bool: True se a porta abriu com sucesso dentro do prazo, False caso contrário.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except (socket.timeout, ConnectionRefusedError):
            time.sleep(0.1)
    return False


def run_docker_compose(command: str) -> None:
    """
    Executa comandos do Docker Compose lidando com compatibilidade de sintaxe V1/V2.

    Explicação de utilidade:
        No Windows com Docker Desktop recente, o comando clássico 'docker-compose' (com hífen) 
        foi descontinuado em favor de 'docker compose' (espaçado). Esta função tenta a 
        sintaxe antiga e, caso falhe, tenta a sintaxe nova de forma transparente.

    Args:
        command (str): Ação do compose a ser executada ('up' ou 'down').
    """
    if command == "up":
        args_v1 = ["docker-compose", "up", "-d"]
        args_v2 = ["docker", "compose", "up", "-d"]
    else:
        args_v1 = ["docker-compose", "down"]
        args_v2 = ["docker", "compose", "down"]

    try:
        # Tenta executar usando o padrão clássico (V1)
        subprocess.run(args_v1, check=True, cwd=str(ROOT_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            # Fallback para a especificação moderna (V2)
            subprocess.run(args_v2, check=True, cwd=str(ROOT_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            raise RuntimeError(f"Falha crítica ao gerenciar Docker (verifique se o Docker Desktop está aberto): {e}")


def start_process(
    label: str, args: list[str], cwd: Path, env: dict[str, str]
) -> subprocess.Popen:
    """
    Inicia um subprocesso em background de forma isolada e assíncrona.

    Explicação de utilidade:
        Esta função encapsula a criação dos nós e do servidor gRPC. Ao não capturar 
        a saída em um PIPE (mantendo o comportamento original estável), os logs de 
        todos os nós são impressos nativamente em tempo real na tela do terminal.

    Args:
        label (str): Nome identificador do processo para exibição no log de boot.
        args (list[str]): O comando de execução que será invocado no terminal.
        cwd (Path): O diretório de trabalho onde o processo será executado.
        env (dict[str, str]): Dicionário de variáveis de ambiente tratadas.

    Returns:
        subprocess.Popen: O handle/ponteiro de controle do processo criado.
    """
    print(f"[INICIANDO] {label}: {' '.join(args)}")
    return subprocess.Popen(
        args,
        cwd=str(cwd),
        env=env,
    )


def main() -> None:
    """
    Função principal que coordena o ciclo de vida completo do ecossistema DFS.
    """
    env = build_env()
    processes: list[subprocess.Popen] = []

    print(f"\n{'='*60}")
    # Modificação do print original para incluir a assinatura visual de orquestração detalhada
    print("🚀 INICIALIZADOR CORE: Gerenciador de Ciclo de Vida DFS")
    print(f"{'='*60}\n")

    try:
        # === ETAPA 1: PRE-FLIGHT CHECK (Validação de colisão de portas) ===
        print("[1/4] Executando checagem preventiva de portas no host...")
        portas_presas = []
        if is_port_in_use(PORTA_COORDENADOR):
            portas_presas.append(f"Coordenador ({PORTA_COORDENADOR})")
        for nome_no, porta in PORTAS_NOS.items():
            if is_port_in_use(porta):
                portas_presas.append(f"{nome_no} ({porta})")

        if portas_presas:
            print(f"🚨 [ERRO CRÍTICO] Portas em uso detectadas: {', '.join(portas_presas)}")
            print("   O Windows possui processos fantasmas travando estas portas.")
            print("   Abra o Gerenciador de Tarefas e finalize os processos 'python.exe' antigos.\n")
            sys.exit(1)
        print("      -> Sucesso: Todas as portas do projeto estão totalmente livres!")

        # === ETAPA 2: INFRAESTRUTURA DE MENSAGERIA (Docker Kafka) ===
        print("\n[2/4] Solicitando inicialização de Zookeeper e Kafka via Docker...")
        run_docker_compose("up")
        
        print("      -> Aguardando dinamicamente o broker Kafka (Porta 9092) estabilizar...")
        if wait_for_port(9092, timeout=15.0):
            print("      -> Sucesso: Broker Kafka respondendo e pronto para receber tópicos!")
        else:
            print("⚠️  [AVISO] A porta do Kafka demorou a responder. Tentando prosseguir de qualquer forma...")

        # === ETAPA 3: CONTROL PLANE (Coordenador gRPC) ===
        print("\n[3/4] Iniciando Servidor Coordenador (Control Plane)...")
        # O Coordenador DEVE subir primeiro para preparar as tabelas gRPC de registro de nós
        processes.append(
            start_process(
                "coordinator",
                [sys.executable, "-m", "dfs.interface.server"],
                cwd=DFS_DIR,
                env=env,
            )
        )
        
        print("      -> Aguardando ativação da porta gRPC do Coordenador (9100)...")
        wait_for_port(PORTA_COORDENADOR)

        # === ETAPA 4: DATA PLANE (Storage Nodes / Nós de Armazenamento) ===
        print("\n[4/4] Disparando Data Planes em paralelo baseado na configuração...")
        # Itera dinamicamente sobre a lista definida em dfs.config.config.py
        for node_id in NODE_ORDER:
            processes.append(
                start_process(
                    node_id,
                    [
                        sys.executable,
                        "-m",
                        "dfs.interface.storage_node",
                        "--node-id",
                        node_id,
                    ],
                    cwd=DFS_DIR,
                    env=env,
                )
            )
            # Pequeno delay de milissegundos apenas para evitar sobrecarga de concorrência inicial no S.O.
            time.sleep(0.1)

        print(f"\n{'='*60}")
        print("✅ ECOSSISTEMA DFS TOTALMENTE OPERACIONAL!")
        print("   Mantenha esta janela aberta para monitorar as mensagens em tempo real.")
        print("   Para interromper o cluster e limpar os containers, use Ctrl+C.")
        print(f"{'='*60}\n")

        # Mantém a thread principal viva monitorando os subprocessos
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n🛑 [SINAL CAPTURADO] Interrupção solicitada pelo usuário. Iniciando encerramento...")
    except Exception as e:
        print(f"\n🚨 [ERRO CRÍTICO] Falha inesperada durante a execução do cluster: {e}")

    finally:
        # === ETAPA FINAL DE LIMPEZA E DESALOCAÇÃO DE MEMÓRIA ===
        print("\nFinalizando processos ativos do Python (Control/Data Planes)...")
        # Envia sinal de encerramento amigável (terminate) para os nós e coordenador
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()

        # Aguarda a confirmação de encerramento por até 3 segundos, se travar, força o Kill
        for proc in processes:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

        print("Solicitando desligamento e limpeza da infraestrutura Docker...")
        try:
            run_docker_compose("down")
            print("🟩 Infraestrutura Docker removida com sucesso!")
        except Exception as e:
            print(f"⚠️  Não foi possível desligar os containers automaticamente: {e}")

        print(f"\n{'='*60}")
        print("🏁 Cluster DFS encerrado de forma limpa e segura. Até logo!")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    main()