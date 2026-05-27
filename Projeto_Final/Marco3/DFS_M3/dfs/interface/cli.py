"""
DESCRIÇÃO GERAL:
Interface de linha de comando do DFS adaptada para a arquitetura gRPC.
"""

import argparse
import shlex
from pathlib import Path

from dfs.client import DFSClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dfs",
        description="Cliente do DFS distribuído",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    put = sub.add_parser("put", help="Envia um arquivo para o DFS")
    put.add_argument("source", help="arquivo local de origem")
    put.add_argument("target", help="caminho lógico no DFS")

    get = sub.add_parser("get", help="Lê um arquivo do DFS")
    get.add_argument("path", help="caminho lógico no DFS")
    get.add_argument("output", nargs="?", default=None, help="arquivo local de saída")

    sub.add_parser("list", help="Lista entradas no DFS")

    rm = sub.add_parser("rm", help="Remove um arquivo do DFS")
    rm.add_argument("path", help="caminho lógico no DFS")

    sub.add_parser("menu", help="Abre o menu interativo do DFS")

    return parser


def print_menu() -> None:
    commands = [
        ("put <file> <dfs_path>", "Envia arquivo ao DFS."),
        ("get <dfs_path> [local_file]", "Baixa arquivo do DFS."),
        ("rm <dfs_path>", "Remove arquivo do DFS."),
        ("list", "Lista entradas no DFS."),
        ("exit | quit", "Encerra sessão."),
    ]
    examples = [
        "put teste.txt docs/teste.txt",
        "get docs/teste.txt copia.txt",
        "rm docs/teste.txt",
        "list",
    ]

    print("\n" + "=" * 110)
    print("SISTEMA DE ARQUIVOS DISTRIBUÍDO (DFS) - MENU INTERATIVO")
    print("=" * 110)

    left_title, right_title = "COMANDOS DISPONÍVEIS", "EXEMPLOS"
    print(f"{left_title:<58}{right_title:<50}")
    print(f"{'-' * 56}  {'-' * 48}")

    max_rows = max(len(commands), len(examples))
    for i in range(max_rows):
        left = f"{commands[i][0]:<28} {commands[i][1]:<27}" if i < len(commands) else ""
        right = examples[i] if i < len(examples) else ""
        print(f"{left:<58}{right}")

    print("=" * 110)
    print("Digite 'help', 'menu' ou '?' para reexibir o menu a qualquer momento.")
    print("=" * 110 + "\n")


def _run_single_command(client: DFSClient, args: argparse.Namespace) -> None:
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

    if args.command == "menu":
        interactive_menu()
        return

    print("Comando inválido.")
    print_menu()


def interactive_menu() -> None:
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
                
                lowered = raw.lower()
                if lowered in {"exit", "quit"}:
                    print("Encerrando sessão.")
                    break
                if lowered in {"help", "menu", "?"}:
                    print_menu()
                    continue

                try:
                    argv = shlex.split(raw)
                    if argv:
                        argv[0] = argv[0].lower()
                    args = parser.parse_args(argv)
                except SystemExit:
                    print("Entrada inválida. Digite 'help' para ver os comandos.\n")
                    continue
                except ValueError as exc:
                    print(f"Erro ao interpretar comando: {exc}\n")
                    continue

                if args.command is None:
                    print("Nenhum comando informado. Digite 'help'.\n")
                    continue

                _run_single_command(client, args)
                print()

    except Exception as exc:
        print(f"Erro na sessão interativa: {exc}")


def main(argv=None) -> None:
    parser = build_parser()
    argv = argv or []

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