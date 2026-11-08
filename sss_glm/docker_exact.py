# SPDX-License-Identifier: AGPL-3.0-only
"""Exact-config container recreation. Never approximates a short docker run.

Public output is redacted (env values stripped). Full inspect stays on the
VPS under a root-only snapshot dir. Tests use synthetic fixtures only.
"""
from __future__ import annotations

import json
import os
from copy import deepcopy

FORBIDDEN = ("harvey",)
SSS_NAME = "harvey-sss"
HERMES_NAME = "hermes-agent"
CHANNEL = "/opt/sss-glm-channel"
SECRET_MARKERS = (
    "PASSWORD", "SECRET", "TOKEN", "API_KEY", "KEY", "SMTP", "OPENROUTER",
    "AWS_", "PRIVATE", "CREDENTIAL", "GPG",
)

# HostConfig keys this mapper knows how to reproduce. Anything else that is
# meaningfully set is a fail-closed blocker.
SUPPORTED_HOSTCONFIG = {
    "AutoRemove", "Binds", "BlkioDeviceReadBps", "BlkioDeviceReadIOps",
    "BlkioDeviceWriteBps", "BlkioDeviceWriteIOps", "BlkioWeight",
    "BlkioWeightDevice", "CapAdd", "CapDrop", "Cgroup", "CgroupParent",
    "CgroupnsMode", "ConsoleSize", "ContainerIDFile", "CpuCount",
    "CpuPercent", "CpuPeriod", "CpuQuota", "CpuRealtimePeriod",
    "CpuRealtimeRuntime", "CpuShares", "CpusetCpus", "CpusetMems",
    "DeviceCgroupRules", "DeviceRequests", "Devices", "Dns", "DnsOptions",
    "DnsSearch", "ExtraHosts", "GroupAdd", "IOMaximumBandwidth",
    "IOMaximumIOps", "Init", "IpcMode", "Isolation", "KernelMemory",
    "KernelMemoryTCP", "Links", "LogConfig", "MaskedPaths", "Memory",
    "MemoryReservation", "MemorySwap", "MemorySwappiness", "Mounts",
    "NanoCpus", "NetworkMode", "OomKillDisable", "OomScoreAdj", "PidMode",
    "PidsLimit", "PortBindings", "Privileged", "PublishAllPorts",
    "ReadonlyPaths", "ReadonlyRootfs", "RestartPolicy", "Runtime",
    "SecurityOpt", "ShmSize", "StorageOpt", "Sysctls", "Tmpfs", "UTSMode",
    "Ulimits", "UsernsMode", "VolumeDriver", "VolumesFrom", "Annotations",
    "Count",
}

SSS_EXTRA_MOUNTS = (
    {"Type": "bind", "Source": f"{CHANNEL}/requests",
     "Destination": "/var/sss-glm/requests", "RW": True},
    {"Type": "bind", "Source": f"{CHANNEL}/responses",
     "Destination": "/var/sss-glm/responses", "RW": False},
)
HERMES_EXTRA_MOUNTS = (
    {"Type": "bind", "Source": f"{CHANNEL}/requests",
     "Destination": "/var/sss-glm/requests", "RW": False},
    {"Type": "bind", "Source": f"{CHANNEL}/responses",
     "Destination": "/var/sss-glm/responses", "RW": True},
    {"Type": "bind", "Source": f"{CHANNEL}/ledger",
     "Destination": "/var/sss-glm/ledger", "RW": True},
)


def allowed_target(name: str) -> None:
    base = name.split(".", 1)[0]
    if name in FORBIDDEN or base == "harvey" or base not in (SSS_NAME, HERMES_NAME):
        raise SystemExit(
            f"refusing {name}: may only recreate {SSS_NAME} or {HERMES_NAME}, "
            "never the custom Harvey dashboard container"
        )


def extra_mounts_for(name: str) -> tuple[dict, ...]:
    if name == SSS_NAME:
        return SSS_EXTRA_MOUNTS
    if name == HERMES_NAME:
        return HERMES_EXTRA_MOUNTS
    raise SystemExit(f"no extra mounts for {name}")


def _set(value) -> bool:
    if value is None or value is False:
        return False
    if value in (0, 0.0, -1, "", b""):
        return False
    if value in ([], {}, [0, 0], (0, 0)):
        return False
    if isinstance(value, dict) and not any(_set(v) for v in value.values()):
        return False
    return True


def _secretish(key: str) -> bool:
    u = key.upper()
    return any(s in u for s in SECRET_MARKERS)


def redact_env(env: list[str] | None) -> list[str]:
    out = []
    for item in env or []:
        key = item.split("=", 1)[0]
        out.append(f"{key}=<redacted>" if _secretish(key) else item)
    return out


def env_keys(info: dict) -> list[str]:
    return [i.split("=", 1)[0] for i in (info.get("Config") or {}).get("Env") or []]


def write_env_file(info: dict, path: str) -> None:
    """Root-only env file. Never copied into public artifacts."""
    lines = list((info.get("Config") or {}).get("Env") or [])
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def redact_inspect(info: dict) -> dict:
    """Drop secret values. Safe to print or store next to a public plan."""
    data = deepcopy(info)
    cfg = data.get("Config") or {}
    if "Env" in cfg:
        cfg["Env"] = redact_env(cfg.get("Env"))
    return data


def unsupported_hostconfig(info: dict) -> list[str]:
    host = info.get("HostConfig") or {}
    bad = []
    for key, value in host.items():
        if key in SUPPORTED_HOSTCONFIG:
            continue
        if _set(value):
            bad.append(key)
    return sorted(bad)


def mounts_from_inspect(info: dict) -> list[dict]:
    out = []
    for m in info.get("Mounts") or []:
        typ = m.get("Type") or "bind"
        dest = m.get("Destination")
        if not dest:
            raise SystemExit("mount missing Destination")
        item = {
            "Type": typ,
            "Destination": dest,
            "RW": bool(m.get("RW", True)),
            "Propagation": m.get("Propagation") or "",
            "Driver": m.get("Driver") or "",
        }
        if typ == "bind":
            src = m.get("Source") or m.get("Name")
            if not src:
                raise SystemExit(f"bind mount {dest} missing Source")
            item["Source"] = src
        elif typ == "volume":
            name = m.get("Name")
            if not name:
                raise SystemExit(
                    f"volume mount {dest} has no Name; cannot preserve anonymous volume"
                )
            item["Source"] = name
        elif typ == "tmpfs":
            item["Source"] = ""
        else:
            raise SystemExit(f"unsupported mount type {typ} at {dest}")
        out.append(item)
    return out


def add_mounts(existing: list[dict], extra: tuple[dict, ...]) -> list[dict]:
    dests = {m["Destination"] for m in existing}
    out = list(existing)
    for m in extra:
        if m["Destination"] in dests:
            raise SystemExit(f"extra mount collides with {m['Destination']}")
        item = {
            "Type": m["Type"],
            "Source": m["Source"],
            "Destination": m["Destination"],
            "RW": bool(m.get("RW", True)),
            "Propagation": m.get("Propagation") or "",
            "Driver": m.get("Driver") or "",
        }
        out.append(item)
        dests.add(item["Destination"])
    return out


def networks_from_inspect(info: dict) -> dict:
    mode = (info.get("HostConfig") or {}).get("NetworkMode") or "bridge"
    nets = (info.get("NetworkSettings") or {}).get("Networks") or {}
    primary = mode
    extra = []
    aliases = {}
    for name, cfg in nets.items():
        als = [a for a in (cfg or {}).get("Aliases") or [] if a]
        aliases[name] = als
        if name != primary and name not in ("bridge", "host", "none"):
            extra.append(name)
        elif primary in ("bridge", "default", "host", "none") and name not in (
            "bridge", "host", "none", primary,
        ):
            extra.append(name)
    # If NetworkMode is a user-defined network it is the primary; remaining are extra.
    extra = [n for n in extra if n != primary]
    return {"primary": primary, "extra": extra, "aliases": aliases}


def port_publishes(info: dict) -> list[str]:
    bindings = (info.get("HostConfig") or {}).get("PortBindings") or {}
    out = []
    for container_port, hosts in bindings.items():
        for h in hosts or []:
            ip = h.get("HostIp") or ""
            hp = h.get("HostPort") or ""
            if not hp:
                raise SystemExit(f"port {container_port} missing HostPort")
            spec = f"{ip}:{hp}:{container_port}" if ip else f"{hp}:{container_port}"
            out.append(spec)
    return out


def fingerprint(info: dict) -> dict:
    """Comparable, secret-free snapshot of identity-critical config."""
    cfg = info.get("Config") or {}
    host = info.get("HostConfig") or {}
    health = cfg.get("Healthcheck") or {}
    mounts = []
    for m in mounts_from_inspect(info):
        mounts.append((m["Type"], m["Source"], m["Destination"], m["RW"]))
    nets = networks_from_inspect(info)
    labels = dict(cfg.get("Labels") or {})
    return {
        "user": cfg.get("User") or "",
        "cmd": list(cfg.get("Cmd") or []),
        "entrypoint": list(cfg.get("Entrypoint") or []),
        "workdir": cfg.get("WorkingDir") or "",
        "hostname": cfg.get("Hostname") or "",
        "stop_signal": cfg.get("StopSignal") or "",
        "restart": (host.get("RestartPolicy") or {}).get("Name") or "",
        "privileged": bool(host.get("Privileged")),
        "cap_add": list(host.get("CapAdd") or []),
        "cap_drop": list(host.get("CapDrop") or []),
        "security_opt": list(host.get("SecurityOpt") or []),
        "ports": port_publishes(info),
        "mounts": sorted(mounts),
        "network_primary": nets["primary"],
        "network_extra": sorted(nets["extra"]),
        "labels": labels,
        "health_test": list(health.get("Test") or []),
        "init": bool(host.get("Init")),
        "readonly_rootfs": bool(host.get("ReadonlyRootfs")),
        "shm_size": host.get("ShmSize") or 0,
        "runtime": host.get("Runtime") or "",
        "pid_mode": host.get("PidMode") or "",
        "ipc_mode": host.get("IpcMode") or "",
        "extra_hosts": list(host.get("ExtraHosts") or []),
        "group_add": list(host.get("GroupAdd") or []),
        "sysctls": dict(host.get("Sysctls") or {}),
        "ulimits": host.get("Ulimits") or [],
        "memory": host.get("Memory") or 0,
        "nano_cpus": host.get("NanoCpus") or 0,
    }


def compare_fingerprints(before: dict, after: dict, extra: tuple[dict, ...]) -> list[str]:
    """Return mismatch reasons. extra mounts must appear on after only."""
    reasons = []
    want_mounts = set(before["mounts"])
    for m in extra:
        want_mounts.add((m["Type"], m["Source"], m["Destination"], bool(m.get("RW", True))))
    if set(after["mounts"]) != want_mounts:
        reasons.append(
            f"mounts changed: have={after['mounts']!r} want={sorted(want_mounts)!r}"
        )
    skip = {"mounts"}
    for key in before:
        if key in skip:
            continue
        if before[key] != after.get(key):
            reasons.append(f"{key}: {before[key]!r} -> {after.get(key)!r}")
    return reasons


def blockers(name: str, info: dict) -> list[str]:
    allowed_target(name)
    host = info.get("HostConfig") or {}
    bad = []
    if host.get("Privileged"):
        bad.append("privileged")
    mounts = mounts_from_inspect(info)
    if any("docker.sock" in (m.get("Source") or "") for m in mounts):
        bad.append("docker.sock")
    bad.extend(f"unsupported HostConfig.{k}" for k in unsupported_hostconfig(info))
    return bad


def ns_to_duration(ns: int | None) -> str | None:
    if not ns:
        return None
    sec = int(ns) / 1_000_000_000
    if sec >= 1 and abs(sec - int(sec)) < 1e-9:
        return f"{int(sec)}s"
    ms = int(ns) / 1_000_000
    return f"{int(ms)}ms"


def api_mounts(mounts: list[dict]) -> list[dict]:
    """Engine API HostConfig.Mounts (Target, not Destination)."""
    out = []
    for m in mounts:
        spec = {
            "Type": m["Type"],
            "Target": m["Destination"],
            "ReadOnly": not m["RW"],
        }
        if m["Type"] != "tmpfs":
            spec["Source"] = m["Source"]
        if m["Type"] == "bind" and m.get("Propagation"):
            spec["BindOptions"] = {"Propagation": m["Propagation"]}
        out.append(spec)
    return out


def create_body(name: str, info: dict, *, image: str, extra: tuple[dict, ...]) -> dict:
    """POST /containers/create body. Full Entrypoint array + Cmd, never flattened.

    Docker CLI ``--entrypoint`` only takes the executable. The Engine API
    documents Entrypoint as a string array, which is required for
    ``['/init', '/opt/hermes/docker/main-wrapper.sh']``.
    """
    allowed_target(name)
    cfg = info.get("Config") or {}
    host_in = info.get("HostConfig") or {}
    host = {k: deepcopy(v) for k, v in host_in.items() if k in SUPPORTED_HOSTCONFIG}
    ep = cfg.get("Entrypoint")
    if ep is None:
        entrypoint = None
    else:
        entrypoint = list(ep)
    cmd = list(cfg.get("Cmd") or [])
    mounts = api_mounts(add_mounts(mounts_from_inspect(info), extra))
    host.pop("Binds", None)
    host["Mounts"] = mounts
    nets = networks_from_inspect(info)
    aliases = [a for a in (nets["aliases"].get(nets["primary"]) or []) if a]
    body = {
        "Hostname": cfg.get("Hostname") or "",
        "User": cfg.get("User") or "",
        "Env": list(cfg.get("Env") or []),
        "Cmd": cmd,
        "Entrypoint": entrypoint,
        "Image": image,
        "WorkingDir": cfg.get("WorkingDir") or "",
        "Labels": dict(cfg.get("Labels") or {}),
        "StopSignal": cfg.get("StopSignal") or "",
        "ExposedPorts": dict(cfg.get("ExposedPorts") or {}),
        "HostConfig": host,
        "NetworkingConfig": {
            "EndpointsConfig": {
                nets["primary"]: {"Aliases": aliases},
            }
        },
    }
    health = cfg.get("Healthcheck")
    if health:
        body["Healthcheck"] = health
    return body


def redact_create_body(body: dict) -> dict:
    data = deepcopy(body)
    data["Env"] = [e.split("=", 1)[0] for e in (data.get("Env") or [])]
    return data


def engine_api_create(name: str, body: dict, *, sock: str = "/var/run/docker.sock") -> str:
    """Create a container via the Docker Engine API unix socket. Returns Id."""
    import socket
    from http.client import HTTPConnection

    class UnixHTTPConnection(HTTPConnection):
        def __init__(self, path: str):
            super().__init__("localhost")
            self._path = path

        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(self._path)

    from urllib.parse import quote

    raw = json.dumps(body).encode("utf-8")
    conn = UnixHTTPConnection(sock)
    try:
        conn.request(
            "POST",
            "/v1.41/containers/create?name=" + quote(name, safe=""),
            body=raw,
            headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
        )
        resp = conn.getresponse()
        payload = resp.read().decode("utf-8")
        if resp.status not in (201, 200):
            raise SystemExit(f"engine API create {resp.status}: {payload[:500]}")
        return json.loads(payload).get("Id") or ""
    finally:
        conn.close()


def public_plan(name: str, info: dict, extra: tuple[dict, ...],
                argv: list[str] | None = None, body: dict | None = None) -> dict:
    fp = fingerprint(info)
    return {
        "name": name,
        "scope": "sss-worker-only" if name == SSS_NAME else "hermes-worker-only",
        "leaves_alone": ["harvey", "harvey.dmtoclose.com", "harvey-cloudflared"],
        "original_image": (info.get("Config") or {}).get("Image"),
        "user": fp["user"],
        "cmd": fp["cmd"],
        "entrypoint": fp["entrypoint"],
        "workdir": fp["workdir"],
        "restart": fp["restart"],
        "privileged": fp["privileged"],
        "ports": fp["ports"],
        "mounts": [list(x) for x in fp["mounts"]],
        "extra_mounts": [
            [m["Type"], m["Source"], m["Destination"], bool(m.get("RW", True))]
            for m in extra
        ],
        "network_primary": fp["network_primary"],
        "network_extra": fp["network_extra"],
        "labels": fp["labels"],
        "health_test": fp["health_test"],
        "security_opt": fp["security_opt"],
        "cap_add": fp["cap_add"],
        "cap_drop": fp["cap_drop"],
        "env_keys": env_keys(info),
        "blockers": blockers(name, info),
        "create_method": "engine-api",
        "engine_api_redacted": redact_create_body(body) if body else None,
        "docker_create_redacted": _redact_argv(argv or []),
        "writable_layer": "docker commit snapshot of current container; original kept stopped as .pre-glm",
    }


def _redact_argv(argv: list[str]) -> list[str]:
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


def load_inspect(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return data[0]
    return data
