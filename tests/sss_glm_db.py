# SPDX-License-Identifier: AGPL-3.0-only
"""Minimal sqlite schema for SSS GLM tests. Fictional .example hosts only."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from sss_glm.bind import migrate


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        create table prospects(
          id text primary key, company_id text, email text, status text default 'new'
        );
        create table companies(id text primary key, name text, domain text);
        create table outbox(
          id text primary key, prospect_id text, step integer, kind text,
          to_email text, subject text, body text, status text,
          sent_at text, error text, send_at text
        );
        create table v6_suppression(email text, prospect_id text);
        create table conversations(id text primary key, prospect_id text);
        create table sss_evidence(
          id integer primary key autoincrement, host text, check_name text,
          usable integer, kind text, verified_at text, proof text
        );
        """
    )
    migrate(c)
    return c


def seed_proof(c, host: str, finding: str, *, at: str | None = None) -> str:
    at = at or datetime.now(timezone.utc).isoformat()
    c.execute(
        "insert into sss_evidence(host, check_name, usable, kind, verified_at, proof) "
        "values(?,?,1,'primary',?,?)",
        (host, finding, at, "primary proof fixture"),
    )
    return at


def seed_prospect(c, *, pid="p1", email="owner@dental.example",
                  domain="dental.example", status="new"):
    c.execute("insert into companies(id,name,domain) values(?,?,?)",
              ("c-" + pid, "Fixture Clinic", domain))
    c.execute(
        "insert into prospects(id,company_id,email,status) values(?,?,?,?)",
        (pid, "c-" + pid, email, status),
    )
