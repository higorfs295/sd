"""
DESCRIÇÃO GERAL:
Interface de linha de comando do DFS.

Este módulo agora oferece dois modos de uso:
1) modo direto, com comandos únicos:
   python run_cli.py put origem.txt docs/origem.txt

2) modo interativo, com menu e conexão persistente:
   python run_cli.py

No modo interativo, a mesma conexão TCP com o coordenador fica aberta
enquanto a sessão estiver ativa.
"""

import argparse
import shlex
from pathlib import Path

from dfs.client import DFSClient


def build_parser() -> argparse.ArgumentParser:
    """
    Monta a interface de linha de comando.

    A CLI aceita comandos diretos ou, no modo interativo, entradas digitadas
    pelo usuário a partir do menu.
    """
    parser = argparse.ArgumentParser(
        prog="dfs",
        description="Cliente do DFS distribuído",
    )

    sub = parser.add_subparsers(dest="command", required=False)

    # ------------------------------------------------------------
    # PUT
    # ------------------------------------------------------------
    put = sub.add_parser("put", help="Envia um arquivo para o DFS")
    put.add_argument("source", help="arquivo local de origem")
    put.add_argument("target", help="caminho lógico no DFS")

    # ------------------------------------------------------------
    # GET
    # ------------------------------------------------------------
    get = sub.add_parser("get", help="Lê um arquivo do DFS")
    get.add_argument("path", help="caminho lógico no DFS")
    get.add_argument(
        "output",
        nargs="?",
        default=None,
        help="arquivo local de saída",
    )

    # ------------------------------------------------------------
    # LIST
    # ------------------------------------------------------------
    sub.add_parser("list", help="Lista entradas no DFS")

    # ------------------------------------------------------------
    # RM
    # ------------------------------------------------------------
    rm = sub.add_parser("rm", help="Remove um arquivo do DFS")
    rm.add_argument("path", help="caminho lógico no DFS")

    # ------------------------------------------------------------
    # MKDIR
    # ------------------------------------------------------------
    mkdir = sub.add_parser("mkdir", help="Cria um diretório lógico no DFS")
    mkdir.add_argument("path", help="caminho lógico do diretório")

    # ------------------------------------------------------------
    # RMDIR (se você já adicionou esse comando)
    # ------------------------------------------------------------
    rmdir = sub.add_parser("rmdir", help="Remove um diretório lógico vazio do DFS")
    rmdir.add_argument("path", help="caminho lógico do diretório")

    # ------------------------------------------------------------
    # MENU INTERATIVO
    # ------------------------------------------------------------
    sub.add_parser("menu", help="Abre o menu interativo do DFS")

    return parser

def print_menu() -> None:
    """
    Exibe o menu principal do DFS em formato tabular.

    Layout:
    - comandos à esquerda;
    - exemplos à direita.
    """

    commands = [
        ("put <origem> <dfs_path>", "Envia arquivo ao DFS"),
        ("get <dfs_path> [saida]", "Baixa arquivo do DFS"),
        ("rm <dfs_path>", "Remove arquivo"),
        ("list", "Lista entradas"),
        ("mkdir <dfs_path>", "Cria diretório"),
        ("rmdir <dfs_path>", "Remove diretório vazio"),
        ("exit | quit", "Encerra sessão"),
    ]

    examples = [
        "put teste.txt docs/teste.txt",
        "get docs/teste.txt copia.txt",
        "mkdir docs",
        "list",
        "rm docs/teste.txt",
        "rmdir docs",
    ]

    # ============================================================
    # CABEÇALHO
    # ============================================================

    print()

    print("=" * 110)
    print("DFS DISTRIBUÍDO - MENU INTERATIVO")
    print("=" * 110)

    # ============================================================
    # TÍTULOS
    # ============================================================

    left_title = "COMANDOS DISPONÍVEIS"
    right_title = "EXEMPLOS"

    print(
        f"{left_title:<58}"
        f"{right_title:<50}"
    )

    print(
        f"{'-' * 56}  "
        f"{'-' * 48}"
    )

    # ============================================================
    # LINHAS
    # ============================================================

    max_rows = max(len(commands), len(examples))

    for i in range(max_rows):

        # ------------------------
        # BLOCO ESQUERDO
        # ------------------------

        if i < len(commands):
            cmd, desc = commands[i]

            left = f"{cmd:<28} {desc:<27}"
        else:
            left = ""

        # ------------------------
        # BLOCO DIREITO
        # ------------------------

        if i < len(examples):
            right = examples[i]
        else:
            right = ""

        print(f"{left:<58}{right}")

    # ============================================================
    # RODAPÉ
    # ============================================================

    print("=" * 110)
    print(
        "Modo interativo ativo | conexão persistente com o coordenador"
    )
    print("=" * 110)
    print()

def _run_single_command(client: DFSClient, args: argparse.Namespace) -> None:
    """
    Executa um comando já interpretado pelo argparse.
    """
    if args.command == "put":
        source = Path(args.source)

        if not source.exists():
            print(f"Arquivo local não encontrado: {source}")
            return

        data = source.read_bytes()
        response = client.send("PUT", path=args.target, data=data)
        print(response.message)
        return

    if args.command == "get":
        response = client.send("GET", path=args.path)

        if not response.ok:
            print(response.message)
            return

        output = args.output or Path(args.path).name or "saida.bin"
        Path(output).write_bytes(response.data)
        print(f"{response.message} -> salvo em {output}")
        return

    if args.command == "list":
        response = client.send("LIST")

        if not response.ok:
            print(response.message)
            return

        if not response.entries:
            print("(vazio)")
            return

        for entry in response.entries:
            print(entry)
        return

    if args.command == "rm":
        response = client.send("DELETE", path=args.path)
        print(response.message)
        return

    if args.command == "mkdir":
        response = client.send("MKDIR", path=args.path)
        print(response.message)
        return

    if args.command == "rmdir":
        response = client.send("RMDIR", path=args.path)
        print(response.message)
        return

    if args.command == "menu":
        interactive_menu()
        return

    print("Comando inválido.")
    print_menu()


def interactive_menu() -> None:
    """
    Abre um shell interativo com conexão persistente.

    A mesma conexão TCP é reutilizada até o usuário sair.
    """
    parser = build_parser()

    print_menu()

    try:
        with DFSClient() as client:
            while True:
                try:
                    raw = input("dfs> ").strip()
                except EOFError:
                    print("\nEncerrando sessão.")
                    break

                if not raw:
                    continue

                lowered = raw.lower().strip()

                if lowered in {"exit", "quit"}:
                    print("Encerrando sessão.")
                    break

                if lowered in {"help", "menu", "?"}:
                    print_menu()
                    continue

                try:
                    argv = shlex.split(raw)
                    args = parser.parse_args(argv)
                except SystemExit:
                    # O argparse pode tentar sair quando a entrada é inválida.
                    print("Entrada inválida. Digite 'help' para ver os comandos.")
                    continue
                except ValueError as exc:
                    print(f"Erro ao interpretar comando: {exc}")
                    continue

                if args.command is None:
                    print("Nenhum comando informado. Digite 'help'.")
                    continue

                _run_single_command(client, args)

    except Exception as exc:
        print(f"Erro na sessão interativa: {exc}")


def main(argv=None) -> None:
    """
    Executa a CLI.

    Regras:
    - sem argumentos -> abre menu interativo;
    - com argumentos -> executa comando único;
    - 'menu' -> abre menu interativo explicitamente.
    """
    parser = build_parser()

    if argv is None:
        argv = []

    # Sem argumentos: comportamento interativo.
    if len(argv) == 0:
        interactive_menu()
        return

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return

    if args.command == "menu":
        interactive_menu()
        return

    with DFSClient() as client:
        _run_single_command(client, args)


if __name__ == "__main__":
    main()