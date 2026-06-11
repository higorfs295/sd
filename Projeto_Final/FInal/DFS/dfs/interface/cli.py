# dfs/interface/cli.py
"""
DESCRIÇÃO GERAL:
Interface de linha de comando do DFS (Marco 3, modelo gateway).

Mantém a EXPERIÊNCIA VISUAL da CLI anterior (menu interativo, tabela de comandos,
prompt `dfs>`, help/menu/exit) e o CLIENTE PERSISTENTE durante a sessão
interativa, mas por baixo usa o fluxo de chamadas do Marco 3:

  PUT : RequestUpload (coordenador) -> SetUploadPlan (ingress) -> UploadFile (ingress)
  GET : RequestDownload (coordenador) -> SetDownloadPlan (egress) -> DownloadFile (egress)
  RM  : DeleteFile (coordenador)
  LIST: ListFiles (coordenador)

Persistência: na sessão interativa, abrimos UM ControlClient (canal para o
coordenador) e o reusamos em todos os comandos, fechando só ao sair. O DataClient
é aberto por operação, porque o nó-gateway (ingress/egress) varia conforme o
arquivo — abrir por operação é o comportamento correto, não desperdício.
"""

from __future__ import annotations

import argparse
import shlex
import uuid
from pathlib import Path

import grpc

from dfs.client import ControlClient, DataClient


# =============================================================================
# PARSER
# =============================================================================

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


# =============================================================================
# MENU (visual idêntico ao da CLI anterior)
# =============================================================================

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


# =============================================================================
# EXECUÇÃO DE UM COMANDO (usa um ControlClient já aberto = cliente persistente)
# =============================================================================

def _run_single_command(ctrl: ControlClient, args: argparse.Namespace) -> None:
    try:
        # ----------------------------------------------------------------- PUT
        if args.command == "put":
            source = Path(args.source)
            if not source.exists():
                print(f"Arquivo local não encontrado: {source}")
                return
            data = source.read_bytes()

            grant = ctrl.request_upload(
                args.target, len(data), client_request_id=str(uuid.uuid4())
            )
            if not grant.ok:
                print(f"Falha ao autorizar upload: {grant.message}")
                return

            dc = DataClient(grant.ingress.host, grant.ingress.port)
            try:
                dc.set_upload_plan(grant.upload_id, len(data), grant.chunks)
                res = dc.upload(grant.upload_id, data)
            finally:
                dc.close()
            print(res.message)
            return

        # ----------------------------------------------------------------- GET
        if args.command == "get":
            info = ctrl.request_download(
                args.path, client_request_id=str(uuid.uuid4())
            )
            if not info.ok:
                print(info.message)
                return

            dc = DataClient(info.egress.host, info.egress.port)
            try:
                dc.set_download_plan(info.download_id, info.total_size_bytes, info.chunks)
                data = dc.download(info.download_id)
            finally:
                dc.close()

            output = args.output or Path(args.path).name or "saida.bin"
            Path(output).write_bytes(data)
            print(f"Arquivo baixado ({len(data)} bytes) -> salvo em {output}")
            return

        # ---------------------------------------------------------------- LIST
        if args.command == "list":
            resp = ctrl.list_files()
            if not resp.files:
                print("(vazio)")
                return
            for fe in resp.files:
                nodes = ", ".join(fe.nodes_used) if fe.nodes_used else "-"
                print(
                    f"[FILE] {fe.logical_path}  "
                    f"({fe.chunk_count} chunk(s), "
                    f"{fe.total_size_bytes} bytes, nodes={nodes})"
                )
            return

        # ------------------------------------------------------------------ RM
        if args.command == "rm":
            ack = ctrl.delete_file(args.path)
            print(ack.message)
            return

        # ---------------------------------------------------------------- MENU
        if args.command == "menu":
            interactive_menu()
            return

        print("Comando inválido.")
        print_menu()

    except grpc.RpcError as exc:
        # Mantém a sessão viva se o coordenador/nó estiver fora ou recusar.
        print(f"Erro de comunicação com o cluster: {exc.details()}")
    except Exception as exc:  # noqa: BLE001
        print(f"Erro ao executar comando: {exc}")


# =============================================================================
# SESSÃO INTERATIVA (cliente PERSISTENTE: um ControlClient para toda a sessão)
# =============================================================================

def interactive_menu() -> None:
    parser = build_parser()
    print_menu()

    ctrl = ControlClient()  # aberto UMA vez e reusado em toda a sessão
    try:
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

            _run_single_command(ctrl, args)
            print()
    except Exception as exc:  # noqa: BLE001
        print(f"Erro na sessão interativa: {exc}")
    finally:
        ctrl.close()


# =============================================================================
# ENTRYPOINT
# =============================================================================

def main(argv=None) -> None:
    parser = build_parser()
    argv = argv or []

    # Sem argumentos: abre o menu interativo (com cliente persistente).
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

    # Comando único: abre o ControlClient, executa e fecha.
    ctrl = ControlClient()
    try:
        _run_single_command(ctrl, args)
    finally:
        ctrl.close()


if __name__ == "__main__":
    main()