# SPDX-License-Identifier: AGPL-3.0-only
"""SSS-side channel client: write requests, read responses.

SSS mounts requests RW and responses RO. This module never talks to Docker,
Hermes, or a model. Missing/invalid/held responses fail closed.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from sss_glm.fs import (
    FSError,
    assert_real_dir,
    atomic_create,
    channel_file,
    read_response_file,
)
from sss_glm.protocol import (
    MAX_REQUEST_BYTES,
    ProtocolError,
    canonical_dumps,
    parse_request,
    parse_response,
    request_digest,
)

# Default mounts inside harvey-sss after the narrow recreation.
DEFAULT_REQUESTS = "/var/sss-glm/requests"
DEFAULT_RESPONSES = "/var/sss-glm/responses"


class ChannelError(ProtocolError):
    pass


class Client:
    def __init__(self, requests_dir: str | Path, responses_dir: str | Path,
                 *, poll_s: float = 0.5, timeout_s: float = 85.0):
        self.requests_dir = Path(requests_dir)
        self.responses_dir = Path(responses_dir)
        self.poll_s = poll_s
        self.timeout_s = timeout_s

    @classmethod
    def from_env(cls) -> "Client":
        req = os.environ.get("SSS_GLM_REQUESTS", DEFAULT_REQUESTS)
        resp = os.environ.get("SSS_GLM_RESPONSES", DEFAULT_RESPONSES)
        return cls(req, resp)

    def require_mounts(self) -> None:
        try:
            assert_real_dir(self.requests_dir)
            assert_real_dir(self.responses_dir)
        except FSError as e:
            raise ChannelError("bridge_unavailable", str(e.code)) from e
        # Responses must be non-writable from SSS (RO mount). Best-effort.
        if os.access(self.responses_dir, os.W_OK):
            # Writable responses would let SSS forge pending_review. Fail closed
            # unless tests opt in.
            if os.environ.get("SSS_GLM_ALLOW_WRITABLE_RESPONSES") != "1":
                raise ChannelError("bridge_unavailable", "responses_writable")

    def submit(self, req: dict) -> str:
        self.require_mounts()
        parsed = parse_request(canonical_dumps(req), now=datetime.now(timezone.utc))
        data = canonical_dumps(parsed)
        if len(data) > MAX_REQUEST_BYTES:
            raise ChannelError("request_size")
        name = parsed["id"] + ".json"
        try:
            atomic_create(self.requests_dir, name, data, mode=0o640)
        except FSError as e:
            raise ChannelError(e.code) from e
        return parsed["id"]

    def read(self, ident: str) -> dict:
        name = ident + ".json"
        try:
            channel_file(self.responses_dir, name)
            raw = read_response_file(self.responses_dir, name)
        except FSError as e:
            raise ChannelError(e.code) from e
        resp = parse_response(raw, expect_id=ident)
        return resp

    def wait(self, ident: str, *, expected_sha: str | None = None) -> dict:
        deadline = time.monotonic() + self.timeout_s
        last_err = "timeout"
        while time.monotonic() < deadline:
            try:
                resp = self.read(ident)
                if expected_sha and resp.get("request_sha256") != expected_sha:
                    raise ChannelError("replay", "hash")
                return resp
            except ChannelError as e:
                if e.code in ("path",):
                    last_err = "waiting"
                    time.sleep(self.poll_s)
                    continue
                raise
            except ProtocolError:
                raise
        raise ChannelError("timeout", last_err)


def digest_of(req: dict) -> str:
    return request_digest(req)
