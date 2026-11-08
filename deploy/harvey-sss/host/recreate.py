#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Exact-config recreate of harvey-sss or hermes-agent.

Snapshots the writable layer (docker commit), keeps the original container
stopped as NAME.pre-glm, creates a sibling with the same HostConfig plus GLM
channel mounts, then fail-closes if ports/volumes/networks/labels/health
differ. Default is dry-run. Never prints env secret values. Never sends mail.
Never targets container harvey.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for candidate in (
    HERE.parents[2],  # repo root
    HERE.parent / "overlay" / "data",  # staged tarball
):
    if (candidate / "sss_glm" / "docker_exact.py").is_file():
        sys.path.insert(0, str(candidate))
        break

from sss_glm.docker_exact import (  # noqa: E402
    HERMES_NAME,
    SSS_NAME,
    allowed_target,
    blockers,
    compare_fingerprints,
    create_body,
    engine_api_create,
    extra_mounts_for,
    fingerprint,
    networks_from_inspect,
    public_plan,
    redact_create_body,
    redact_inspect,
    write_env_file,
)

SNAP_ROOT = "/root/sss-glm-snapshots"


def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def inspect_live(name: str) -> dict:
    allowed_target(name)
    raw = _run(["docker", "inspect", name]).stdout
    data = json.loads(raw)
    if not data:
        raise SystemExit(f"container {name} not found")
    return data[0]


def snapshot_image_name(name: str, stamp: str) -> str:
    return f"sss-glm-snap/{name}:{stamp}"


def apply_recreate(name: str, info: dict, *, stamp: str | None = None) -> dict:
    allowed_target(name)
    if os.environ.get("SSS_GLM_CONFIRM_RECREATE") != "harvey-sss-only":
        raise SystemExit(
            "refusing --apply recreate without SSS_GLM_CONFIRM_RECREATE=harvey-sss-only"
        )
    extra = extra_mounts_for(name)
    bad = blockers(name, info)
    if bad:
        raise SystemExit(f"refusing to recreate {name}: {bad}")
    stamp = stamp or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    snap_dir = os.path.join(SNAP_ROOT, stamp)
    os.makedirs(snap_dir, mode=0o700, exist_ok=True)
    redacted_path = os.path.join(snap_dir, f"{name}.inspect.redacted.json")
    with open(redacted_path, "w", encoding="utf-8") as fh:
        json.dump(redact_inspect(info), fh, indent=2)
        fh.write("\n")
    os.chmod(redacted_path, 0o600)
    env_path = os.path.join(snap_dir, f"{name}.env")
    if os.path.exists(env_path):
        os.remove(env_path)
    write_env_file(info, env_path)
    saved_procs = None
    from sss_glm.process_lifecycle import capture as cap_procs, one_listener
    try:
        saved_procs = cap_procs(name)
        proc_path = os.path.join(snap_dir, f"{name}.procs.json")
        with open(proc_path, "w", encoding="utf-8") as fh:
            json.dump(saved_procs, fh, indent=2)
            fh.write("\n")
        os.chmod(proc_path, 0o600)
    except SystemExit as exc:
        if name == SSS_NAME:
            raise
        saved_procs = {"container": name, "capture_error": str(exc)[:300]}
    if name == SSS_NAME:
        one_listener(saved_procs, 5555)
    image = snapshot_image_name(name, stamp)
    _run(["docker", "commit", name, image])
    tmp_name = f"{name}.glmnew"
    body = create_body(tmp_name, info, image=image, extra=extra)
    body_path = os.path.join(snap_dir, f"{name}.create.json")
    with open(body_path, "w", encoding="utf-8") as fh:
        json.dump(redact_create_body(body), fh)
        fh.write("\n")
    os.chmod(body_path, 0o600)
    subprocess.run(["docker", "rm", "-f", tmp_name], check=False, capture_output=True)
    engine_api_create(tmp_name, body)
    nets = networks_from_inspect(info)
    for net in nets["extra"]:
        cmd = ["docker", "network", "connect"]
        for als in nets["aliases"].get(net) or []:
            if als and als != name:
                cmd += ["--alias", als]
        cmd += [net, tmp_name]
        _run(cmd)
    _run(["docker", "stop", name])
    pre = f"{name}.pre-glm"
    subprocess.run(["docker", "rm", "-f", pre], check=False, capture_output=True)
    _run(["docker", "rename", name, pre])
    _run(["docker", "rename", tmp_name, name])
    _run(["docker", "start", name])
    after = inspect_live(name)
    reasons = compare_fingerprints(fingerprint(info), fingerprint(after), extra)
    if reasons:
        subprocess.run(["docker", "stop", name], check=False)
        subprocess.run(["docker", "rename", name, f"{name}.glmfailed"], check=False)
        subprocess.run(["docker", "rename", pre, name], check=False)
        subprocess.run(["docker", "start", name], check=False)
        raise SystemExit("post-recreate compare failed: " + "; ".join(reasons))
    launched = None
    if name == SSS_NAME and saved_procs:
        from sss_glm.process_lifecycle import relaunch_if_sleep_pid1, start_review_server
        launched = {
            "dashboard": relaunch_if_sleep_pid1(name, saved_procs),
            "review": start_review_server(name),
        }
    elif name == HERMES_NAME:
        launched = {
            "note": "PID1 is image Entrypoint (/init + wrapper); children not relaunched",
            "pid1": (saved_procs or {}).get("pid1"),
        }
    return {
        "applied": True,
        "name": name,
        "snapshot_image": image,
        "original_stopped": pre,
        "snapshot_dir": snap_dir,
        "compare": "ok",
        "process": launched,
        "docker_restart": False,
        "entrypoint": list((info.get("Config") or {}).get("Entrypoint") or []),
        "cmd": list((info.get("Config") or {}).get("Cmd") or []),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("target", choices=(SSS_NAME, HERMES_NAME))
    p.add_argument("--apply", action="store_true")
    p.add_argument("--inspect-file", default="",
                   help="Synthetic inspect JSON (tests / dry-run without Docker).")
    args = p.parse_args(argv)
    allowed_target(args.target)
    if args.inspect_file:
        from sss_glm.docker_exact import load_inspect
        info = load_inspect(args.inspect_file)
    else:
        info = inspect_live(args.target)
    extra = extra_mounts_for(args.target)
    body = create_body(
        args.target + ".glmnew", info,
        image="sss-glm-snap/dry-run:local", extra=extra,
    )
    plan = public_plan(args.target, info, extra, body=body)
    print(json.dumps(plan, indent=2))
    if plan["blockers"]:
        print("# blockers; refuse --apply", file=sys.stderr)
        return 2
    if not args.apply:
        print("# dry-run only; pass --apply on the VPS as root", file=sys.stderr)
        return 0
    if os.geteuid() != 0:
        print("--apply requires root on the VPS", file=sys.stderr)
        return 1
    if args.inspect_file:
        print("--apply with --inspect-file is refused (would not be live)", file=sys.stderr)
        return 1
    result = apply_recreate(args.target, info)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
