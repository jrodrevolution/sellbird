#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Rebuild harvey-sss or hermes-agent with GLM channel mounts.

Default is dry-run. Never prints env secret values. Never sends mail.
Does not change outbox rows. Parent runs this on the VPS.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

SSS_NAME = "harvey-sss"
HERMES_NAME = "hermes-agent"
# The custom web app (harvey.dmtoclose.com) is a different container. Never
# recreate, cp into, or remount it from this GLM worker overlay.
FORBIDDEN = ("harvey",)
CHANNEL = "/opt/sss-glm-channel"
SECRET_KEYS = (
    "PASSWORD", "SECRET", "TOKEN", "API_KEY", "KEY", "SMTP", "OPENROUTER",
    "AWS_", "PRIVATE", "CREDENTIAL",
)


def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def allowed_target(name: str) -> None:
    if name in FORBIDDEN or name not in (SSS_NAME, HERMES_NAME):
        raise SystemExit(
            f"refusing {name}: GLM overlay may only recreate {SSS_NAME} or "
            f"{HERMES_NAME}, never the custom Harvey dashboard container"
        )


def inspect(name: str) -> dict:
    allowed_target(name)
    raw = _run(["docker", "inspect", name]).stdout
    data = json.loads(raw)
    if not data:
        raise SystemExit(f"container {name} not found")
    return data[0]


def _secretish(key: str) -> bool:
    u = key.upper()
    return any(s in u for s in SECRET_KEYS)


def binds(info: dict) -> list[str]:
    host = info.get("HostConfig") or {}
    out = list(host.get("Binds") or [])
    for m in info.get("Mounts") or []:
        if m.get("Type") != "bind":
            continue
        src, dst = m.get("Source"), m.get("Destination")
        if not src or not dst:
            continue
        spec = f"{src}:{dst}"
        if not m.get("RW", True):
            spec += ":ro"
        if spec not in out and f"{src}:{dst}:rw" not in out:
            # Prefer the HostConfig.Binds form when present.
            if not any(x.startswith(f"{src}:{dst}") for x in out):
                out.append(spec)
    return out


def ensure_bind(existing: list[str], spec: str) -> list[str]:
    src, rest = spec.split(":", 1)
    dst = rest.split(":")[0]
    kept = [b for b in existing if not b.split(":")[1].split(":")[0] == dst]
    kept.append(spec)
    return kept


def plan_sss(info: dict) -> dict:
    image = (info.get("Config") or {}).get("Image") or "harvey-sss:v3"
    user = (info.get("Config") or {}).get("User") or "1000:1000"
    cmd = (info.get("Config") or {}).get("Cmd") or ["sleep", "infinity"]
    workdir = (info.get("Config") or {}).get("WorkingDir") or "/app"
    network = ((info.get("HostConfig") or {}).get("NetworkMode") or "default")
    restart = ((info.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name") or "no"
    privileged = bool((info.get("HostConfig") or {}).get("Privileged"))
    extra = list((info.get("HostConfig") or {}).get("CapAdd") or [])
    mounts = binds(info)
    mounts = ensure_bind(mounts, f"{CHANNEL}/requests:/var/sss-glm/requests:rw")
    mounts = ensure_bind(mounts, f"{CHANNEL}/responses:/var/sss-glm/responses:ro")
    env_keys = []
    for item in (info.get("Config") or {}).get("Env") or []:
        key = item.split("=", 1)[0]
        env_keys.append(key)
    docker_sock = any("docker.sock" in m for m in mounts)
    return {
        "name": SSS_NAME,
        "image": image,
        "user": user,
        "cmd": cmd,
        "workdir": workdir,
        "network": network,
        "restart": restart,
        "privileged": privileged,
        "cap_add": extra,
        "mounts": mounts,
        "env_keys": env_keys,
        "blockers": (
            (["privileged"] if privileged else [])
            + (["docker.sock"] if docker_sock else [])
        ),
        "scope": "sss-worker-only",
        "leaves_alone": ["harvey", "harvey.dmtoclose.com", "/app/web", "studio", "dashboard.py"],
        "writable_layer_warning": (
            "docker rm+run drops harvey-sss writable-layer files under /app "
            "that are not on a volume. Custom dashboard container 'harvey' is "
            "not this target. Confirm localhost :5556 UI lives in the image "
            "or a volume before --apply."
        ),
    }


def plan_hermes(info: dict) -> dict:
    image = (info.get("Config") or {}).get("Image") or "hermes-agent"
    user = (info.get("Config") or {}).get("User") or "1000:1000"
    cmd = (info.get("Config") or {}).get("Cmd")
    workdir = (info.get("Config") or {}).get("WorkingDir") or "/opt/hermes"
    network = ((info.get("HostConfig") or {}).get("NetworkMode") or "default")
    restart = ((info.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name") or "no"
    privileged = bool((info.get("HostConfig") or {}).get("Privileged"))
    mounts = binds(info)
    mounts = ensure_bind(mounts, f"{CHANNEL}/requests:/var/sss-glm/requests:ro")
    mounts = ensure_bind(mounts, f"{CHANNEL}/responses:/var/sss-glm/responses:rw")
    mounts = ensure_bind(mounts, f"{CHANNEL}/ledger:/var/sss-glm/ledger:rw")
    return {
        "name": HERMES_NAME,
        "image": image,
        "user": user,
        "cmd": cmd,
        "workdir": workdir,
        "network": network,
        "restart": restart,
        "privileged": privileged,
        "mounts": mounts,
        "blockers": ["privileged"] if privileged else [],
    }


def docker_run_argv(plan: dict, env_file: str | None) -> list[str]:
    argv = [
        "docker", "run", "-d",
        "--name", plan["name"],
        "--user", str(plan["user"] or "1000:1000"),
        "--workdir", plan["workdir"],
        "--network", plan["network"],
        "--security-opt", "no-new-privileges:true",
    ]
    if plan.get("restart") and plan["restart"] not in ("no", ""):
        argv += ["--restart", plan["restart"]]
    if env_file:
        argv += ["--env-file", env_file]
    for m in plan["mounts"]:
        argv += ["-v", m]
    argv.append(plan["image"])
    if plan.get("cmd"):
        argv += list(plan["cmd"])
    return argv


def write_env_file(info: dict, path: str) -> None:
    lines = []
    for item in (info.get("Config") or {}).get("Env") or []:
        lines.append(item)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def redact_argv(argv: list[str]) -> list[str]:
    out = []
    skip = False
    for a in argv:
        if skip:
            out.append("<env-file>")
            skip = False
            continue
        if a == "--env-file":
            out.append(a)
            skip = True
            continue
        out.append(a)
    return out


def apply_recreate(name: str, plan: dict, info: dict, env_path: str) -> None:
    allowed_target(name)
    if os.environ.get("SSS_GLM_CONFIRM_RECREATE") != "harvey-sss-only":
        raise SystemExit(
            "refusing --apply recreate without SSS_GLM_CONFIRM_RECREATE=harvey-sss-only"
        )
    if plan["blockers"]:
        raise SystemExit(f"refusing to recreate {name}: {plan['blockers']}")
    if os.path.exists(env_path):
        os.remove(env_path)
    write_env_file(info, env_path)
    subprocess.run(["docker", "stop", name], check=False)
    subprocess.run(["docker", "rm", name], check=False)
    argv = docker_run_argv(plan, env_path)
    subprocess.run(argv, check=True)
    os.remove(env_path)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("target", choices=("harvey-sss", "hermes-agent"))
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)
    info = inspect(args.target)
    plan = plan_sss(info) if args.target == SSS_NAME else plan_hermes(info)
    public = dict(plan)
    argv = docker_run_argv(plan, "/root/sss-glm-recreate.env")
    public["docker_run_redacted"] = redact_argv(argv)
    print(json.dumps(public, indent=2))
    if not args.apply:
        print("# dry-run only; pass --apply on the VPS as root to recreate", file=sys.stderr)
        return 0
    if os.geteuid() != 0:
        print(" --apply requires root on the VPS", file=sys.stderr)
        return 1
    apply_recreate(args.target, plan, info, "/root/sss-glm-recreate.env")
    print(json.dumps({"applied": True, "name": args.target, "blockers": plan["blockers"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
