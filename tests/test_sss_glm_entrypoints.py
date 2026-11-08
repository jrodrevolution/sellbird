# SPDX-License-Identifier: AGPL-3.0-only
"""Documented file launches must import sss_glm with PYTHONPATH unset.

Reproduces the VPS blocker: python3 /app/data/sss_glm/review_server.py puts
the package directory on sys.path, so a 'HERE already in sys.path' check
never inserts /app/data. Smoke every executable entrypoint from a staged
tarball layout, never from the repo root.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "deploy" / "harvey-sss" / "host" / "install.sh"
REVIEW_UNIT = ROOT / "deploy" / "harvey-sss" / "systemd" / "sss-glm-review.service"
WORKER_UNIT = ROOT / "deploy" / "harvey-sss" / "systemd" / "sss-glm-worker.service"
LIFECYCLE = ROOT / "sss_glm" / "process_lifecycle.py"
VERIFY = ROOT / "deploy" / "harvey-sss" / "host" / "verify-install.sh"
REVIEW_SRC = ROOT / "sss_glm" / "review_server.py"

SSS_REVIEW_ARGV = ["python3", "/app/data/sss_glm/review_server.py"]
HERMES_WORKER_SCRIPT = "/opt/hermes/.hermes/scripts/approved/sss_glm/worker_main.py"


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def _stage(tmp: Path) -> Path:
    stage = tmp / "pkg"
    subprocess.run(["bash", str(INSTALL), str(stage)], check=True, cwd=str(tmp),
                   env=_clean_env(), capture_output=True, text=True)
    assert (stage / "overlay" / "data" / "sss_glm" / "review_server.py").is_file()
    assert (stage / "overlay" / "hermes" / "sss_glm" / "worker_main.py").is_file()
    return stage


def _isolated(argv):
    """-I ignores the host editable install so tests cannot accidentally
    import /workspace/sss_glm. Production argv has no -I; the VPS image
    does not have this repo on sys.path."""
    return [sys.executable, "-I"] + list(argv[1:] if argv[:1] == [sys.executable] else argv)


def _run(argv, *, cwd, env, timeout=8):
    return subprocess.run(
        _isolated(argv), cwd=str(cwd), env=env, timeout=timeout,
        capture_output=True, text=True,
    )


def test_review_server_no_longer_skips_parent_when_here_on_path():
    text = REVIEW_SRC.read_text()
    assert "if HERE not in sys.path" not in text
    assert "_PARENT" in text
    assert "os.path.dirname(os.path.dirname" in text


def test_documented_launch_paths_unchanged():
    review = REVIEW_UNIT.read_text()
    worker = WORKER_UNIT.read_text()
    life = LIFECYCLE.read_text()
    verify = VERIFY.read_text()
    assert "python3 /app/data/sss_glm/review_server.py" in review
    assert HERMES_WORKER_SCRIPT in worker
    assert '"python3", "/app/data/sss_glm/review_server.py"' in life
    assert "-w /app/data" in verify
    assert 'python3 -c "import sss_glm.enqueue,sss_glm.bind"' not in verify
    assert "-w /opt/hermes/.hermes/scripts/approved" in verify


def test_subprocess_entrypoints_from_packaged_layout():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        stage = _stage(tmp)
        empty = tmp / "cwd"
        empty.mkdir()
        data = stage / "overlay" / "data"
        approved = stage / "overlay" / "hermes"
        # SSS container layout: /app/data/...
        review_py = data / "sss_glm" / "review_server.py"
        env = _clean_env(SSS_GLM_REVIEW_BIND="127.0.0.1:0")
        proc = subprocess.Popen(
            _isolated([sys.executable, str(review_py)]),
            cwd=str(empty), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            try:
                out, err = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                proc.terminate()
                out, err = proc.communicate(timeout=2)
        finally:
            if proc.poll() is None:
                proc.kill()
        blob = (out or "") + (err or "")
        assert "ModuleNotFoundError" not in blob, blob
        assert "sss-glm-review" in blob, blob

        help_env = _clean_env()
        preview = _run(
            [sys.executable, str(data / "sss_glm_preview.py"), "--help"],
            cwd=empty, env=help_env,
        )
        assert preview.returncode == 0, preview.stderr
        assert "ModuleNotFoundError" not in (preview.stderr + preview.stdout)

        approve = _run(
            [sys.executable, str(data / "sss_glm_approve.py"), "--help"],
            cwd=empty, env=help_env,
        )
        assert approve.returncode == 0, approve.stderr
        assert "ModuleNotFoundError" not in (approve.stderr + approve.stdout)

        db = tmp / "empty.db"
        engine = _run(
            [sys.executable, str(data / "sss_engine.py"), "help", "--db", str(db)],
            cwd=empty, env=help_env, timeout=15,
        )
        assert "ModuleNotFoundError" not in (engine.stderr + engine.stdout), engine.stderr
        assert engine.returncode == 0, engine.stderr

        # Hermes file launch (systemd argv after the interpreter).
        req, resp, led = tmp / "requests", tmp / "responses", tmp / "ledger"
        for p in (req, resp, led):
            p.mkdir()
        worker_env = _clean_env(
            SSS_GLM_REQUESTS=str(req),
            SSS_GLM_RESPONSES=str(resp),
            SSS_GLM_LEDGER=str(led),
        )
        worker = _run(
            [sys.executable, str(approved / "sss_glm" / "worker_main.py")],
            cwd=empty, env=worker_env,
        )
        assert worker.returncode == 0, worker.stderr
        assert "ModuleNotFoundError" not in (worker.stderr + worker.stdout)

        host_env = _clean_env()
        rec = _run(
            [sys.executable, str(stage / "host" / "recreate.py"), "-h"],
            cwd=empty, env=host_env,
        )
        assert rec.returncode == 0, rec.stderr
        rel = _run(
            [sys.executable, str(stage / "host" / "reload_process.py"), "-h"],
            cwd=empty, env=host_env,
        )
        assert rel.returncode == 0, rel.stderr


def test_review_file_launch_fails_without_package_parent(tmp_path):
    """A copy of review_server.py with no sibling package must not import sss_glm."""
    empty = tmp_path / "cwd"
    empty.mkdir()
    lone = tmp_path / "orphan" / "notpkg"
    lone.mkdir(parents=True)
    lone.joinpath("review_server.py").write_text(
        REVIEW_SRC.read_text(), encoding="utf-8"
    )
    bad = _run(
        [sys.executable, str(lone / "review_server.py")],
        cwd=empty, env=_clean_env(), timeout=3,
    )
    assert bad.returncode != 0
    assert "No module named" in (bad.stderr + bad.stdout)
    assert "sss_glm" in (bad.stderr + bad.stdout)
