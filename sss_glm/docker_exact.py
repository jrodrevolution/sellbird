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


def create_argv(name: str, info: dict, *, image: str, env_file: str | None,
                extra: tuple[dict, ...]) -> list[str]:
    """docker create argv that preserves inspect HostConfig/Config."""
    allowed_target(name)
    cfg = info.get("Config") or {}
    host = info.get("HostConfig") or {}
    argv = ["docker", "create", "--name", name]
    user = cfg.get("User")
    if user:
        argv += ["--user", user]
    if cfg.get("WorkingDir"):
        argv += ["--workdir", cfg["WorkingDir"]]
    if cfg.get("Hostname"):
        argv += ["--hostname", cfg["Hostname"]]
    nets = networks_from_inspect(info)
    argv += ["--network", nets["primary"]]
    for als in nets["aliases"].get(nets["primary"]) or []:
        if als and als != name:
            argv += ["--network-alias", als]
    rp = (host.get("RestartPolicy") or {}).get("Name") or ""
    if rp and rp not in ("no", ""):
        argv += ["--restart", rp]
        mx = (host.get("RestartPolicy") or {}).get("MaximumRetryCount") or 0
        if rp == "on-failure" and mx:
            argv[-1] = f"on-failure:{mx}"
    if host.get("Privileged"):
        argv.append("--privileged")
    for cap in host.get("CapAdd") or []:
        argv += ["--cap-add", cap]
    for cap in host.get("CapDrop") or []:
        argv += ["--cap-drop", cap]
    for opt in host.get("SecurityOpt") or []:
        argv += ["--security-opt", opt]
    for spec in port_publishes(info):
        argv += ["-p", spec]
    if host.get("PublishAllPorts"):
        argv.append("-P")
    if host.get("ReadonlyRootfs"):
        argv.append("--read-only")
    if host.get("Init"):
        argv.append("--init")
    if host.get("Runtime"):
        argv += ["--runtime", host["Runtime"]]
    if host.get("PidMode"):
        argv += ["--pid", host["PidMode"]]
    if host.get("IpcMode"):
        argv += ["--ipc", host["IpcMode"]]
    if host.get("UTSMode"):
        argv += ["--uts", host["UTSMode"]]
    if host.get("ShmSize"):
        argv += ["--shm-size", str(host["ShmSize"])]
    if host.get("Memory"):
        argv += ["--memory", str(host["Memory"])]
    if host.get("NanoCpus"):
        argv += ["--cpus", str(int(host["NanoCpus"]) / 1_000_000_000)]
    if host.get("CpuShares"):
        argv += ["--cpu-shares", str(host["CpuShares"])]
    for hst in host.get("ExtraHosts") or []:
        argv += ["--add-host", hst]
    for g in host.get("GroupAdd") or []:
        argv += ["--group-add", str(g)]
    for k, v in (host.get("Sysctls") or {}).items():
        argv += ["--sysctl", f"{k}={v}"]
    for u in host.get("Ulimits") or []:
        argv += ["--ulimit", f"{u.get('Name')}={u.get('Soft')}:{u.get('Hard')}"]
    log = host.get("LogConfig") or {}
    if log.get("Type") and (log.get("Type") != "json-file" or _set(log.get("Config"))):
        argv += ["--log-driver", log["Type"]]
        for k, v in (log.get("Config") or {}).items():
            argv += ["--log-opt", f"{k}={v}"]
    labels = cfg.get("Labels") or {}
    for k, v in labels.items():
        argv += ["--label", f"{k}={v}"]
    health = cfg.get("Healthcheck") or {}
    test = list(health.get("Test") or [])
    if test and test[0] != "NONE":
        cmd = " ".join(test[1:]) if test[0] in ("CMD-SHELL", "CMD") else " ".join(test)
        argv += ["--health-cmd", cmd]
        if health.get("Interval"):
            argv += ["--health-interval", ns_to_duration(health["Interval"])]
        if health.get("Timeout"):
            argv += ["--health-timeout", ns_to_duration(health["Timeout"])]
        if health.get("Retries"):
            argv += ["--health-retries", str(health["Retries"])]
        if health.get("StartPeriod"):
            argv += ["--health-start-period", ns_to_duration(health["StartPeriod"])]
    ep = cfg.get("Entrypoint")
    if ep:
        argv += ["--entrypoint", ep[0]]
        # remaining entrypoint args go before image as we cannot split cleanly;
        # docker --entrypoint takes the executable only; extra args belong in Cmd.
        # If entrypoint has extra args, fail closed.
        if len(ep) > 1:
            raise SystemExit(f"multi-arg Entrypoint not reproduced: {ep!r}")
    if env_file:
        argv += ["--env-file", env_file]
    for m in add_mounts(mounts_from_inspect(info), extra):
        parts = [f"type={m['Type']}", f"destination={m['Destination']}"]
        if m["Type"] != "tmpfs":
            parts.append(f"source={m['Source']}")
        if not m["RW"]:
            parts.append("readonly")
        if m["Type"] == "bind" and m.get("Propagation"):
            parts.append(f"bind-propagation={m['Propagation']}")
        argv += ["--mount", ",".join(parts)]
    argv.append(image)
    cmd = list(cfg.get("Cmd") or [])
    if ep and len(ep) > 1:
        argv += ep[1:]
    argv += cmd
    return argv


def public_plan(name: str, info: dict, extra: tuple[dict, ...], argv: list[str]) -> dict:
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
        "docker_create_redacted": _redact_argv(argv),
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
