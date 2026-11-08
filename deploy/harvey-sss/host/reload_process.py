#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Scoped dashboard/review process control. Never docker restart.

Captures listener metadata (no environ). Reloads the :5555 dashboard by
os.kill of that PID (python interpreter; no kill binary) and relaunching
the same argv. Starts the review server only when :18765 is empty.
In-container mkdir/unlink/chown/revert also use python. Optional host
systemd persist from captured argv.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for candidate in (
    HERE.parents[2],
    HERE.parent / "overlay" / "data",
):
    if (candidate / "sss_glm" / "process_lifecycle.py").is_file():
        sys.path.insert(0, str(candidate))
        break

from sss_glm.process_lifecycle import (  # noqa: E402
    capture,
    persist_unit,
    reload_dashboard,
    start_review_server,
    one_listener,
    container_fs,
    DASHBOARD_PORT,
)


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "action",
        choices=(
            "capture", "reload-dashboard", "start-review", "persist",
            "listening", "fs",
        ),
    )
    p.add_argument("container")
    p.add_argument("--write", default="")
    p.add_argument("--port", type=int, default=DASHBOARD_PORT)
    p.add_argument("--unit", default="")
    p.add_argument("--op", default="", help="fs: mkdir|unlink|chown|revert-sss-glm-inject")
    p.add_argument("--path", action="append", default=[], dest="paths")
    p.add_argument("--owner", default="")
    p.add_argument("--recursive", action="store_true")
    args = p.parse_args(argv)
    if args.action == "fs":
        if args.container not in ("harvey", "harvey-sss", "hermes-agent"):
            raise SystemExit("fs only on harvey, harvey-sss, or hermes-agent")
        op = args.op or "unlink"
        out = container_fs(
            args.container, op, args.paths,
            owner=args.owner, recursive=args.recursive,
        )
        print(json.dumps(out, indent=2))
        return 0
    if args.container not in ("harvey", "harvey-sss"):
        raise SystemExit("only harvey or harvey-sss (sleep-infinity PID1 dashboards)")
    if args.action == "capture":
        meta = capture(args.container)
        print(json.dumps(meta, indent=2))
        if args.write:
            _write_json(args.write, meta)
        return 0
    if args.action == "reload-dashboard":
        out = reload_dashboard(args.container, port=args.port)
        print(json.dumps(out, indent=2))
        return 0
    if args.action == "start-review":
        if args.container != "harvey-sss":
            raise SystemExit("review server only on harvey-sss")
        print(json.dumps(start_review_server(args.container), indent=2))
        return 0
    if args.action == "listening":
        meta = capture(args.container)
        hits = (meta.get("listeners") or {}).get(str(args.port)) or []
        print(json.dumps({
            "container": args.container,
            "port": args.port,
            "listening": bool(hits),
            "count": len(hits),
            "pid1": (meta.get("pid1") or {}).get("argv"),
        }))
        return 0 if hits else 3
    if args.action == "persist":
        meta = capture(args.container)
        proc = one_listener(meta, args.port)
        text = persist_unit(args.container, proc, args.port)
        unit = args.unit or f"/etc/systemd/system/sss-glm-keep-dashboard@{args.container}.service"
        if os.geteuid() != 0:
            print(text)
            return 0
        Path(unit).write_text(text, encoding="utf-8")
        os.chmod(unit, 0o644)
        print(json.dumps({"wrote": unit, "argv": proc["argv"]}))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
