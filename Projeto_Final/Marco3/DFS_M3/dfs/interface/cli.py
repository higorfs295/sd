# dfs/interface/cli.py
"""
CLI do DFS (Marco 3, modelo gateway). Fluxo de duas/três chamadas:
  put <origem> <caminho_logico>
  get <caminho_logico> <destino>
  rm  <caminho_logico>
  ls
"""
from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from dfs.client import ControlClient, DataClient


def cmd_put(source: str, logical_path: str) -> int:
    data = Path(source).read_bytes()
    ctrl = ControlClient()
    try:
        grant = ctrl.request_upload(logical_path, len(data),
                                    client_request_id=str(uuid.uuid4()))
        if not grant.ok:
            print(f"RequestUpload falhou: {grant.message}")
            return 1
        dc = DataClient(grant.ingress.host, grant.ingress.port)
        try:
            dc.set_upload_plan(grant.upload_id, len(data), grant.chunks)
            res = dc.upload(grant.upload_id, data)
        finally:
            dc.close()
        print(res.message if res.ok else f"upload falhou: {res.message}")
        return 0 if res.ok else 1
    finally:
        ctrl.close()


def cmd_get(logical_path: str, dest: str) -> int:
    ctrl = ControlClient()
    try:
        info = ctrl.request_download(logical_path, client_request_id=str(uuid.uuid4()))
        if not info.ok:
            print(f"RequestDownload falhou: {info.message}")
            return 1
        dc = DataClient(info.egress.host, info.egress.port)
        try:
            dc.set_download_plan(info.download_id, info.total_size_bytes, info.chunks)
            data = dc.download(info.download_id)
        finally:
            dc.close()
        Path(dest).write_bytes(data)
        print(f"baixado {len(data)} bytes em {dest}")
        return 0
    finally:
        ctrl.close()


def cmd_rm(logical_path: str) -> int:
    ctrl = ControlClient()
    try:
        ack = ctrl.delete_file(logical_path)
        print(ack.message)
        return 0 if ack.ok else 1
    finally:
        ctrl.close()


def cmd_ls() -> int:
    ctrl = ControlClient()
    try:
        resp = ctrl.list_files()
        for fe in resp.files:
            print(f"{fe.logical_path}\t{fe.total_size_bytes} bytes\t"
                  f"{fe.chunk_count} chunks\tnós={list(fe.nodes_used)}")
        return 0
    finally:
        ctrl.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dfs")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("put"); sp.add_argument("source"); sp.add_argument("logical_path")
    sg = sub.add_parser("get"); sg.add_argument("logical_path"); sg.add_argument("dest")
    sr = sub.add_parser("rm"); sr.add_argument("logical_path")
    sub.add_parser("ls")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "put":
        return cmd_put(args.source, args.logical_path)
    if args.cmd == "get":
        return cmd_get(args.logical_path, args.dest)
    if args.cmd == "rm":
        return cmd_rm(args.logical_path)
    if args.cmd == "ls":
        return cmd_ls()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
