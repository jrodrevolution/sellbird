# SPDX-License-Identifier: AGPL-3.0-only
"""SSS outbox send stats for Studio #sssglm.

Counts status=sent in America/Tijuana. Does not include Hermes 8PM money,
scans, or Harvey totals — those stay on the independent report job.
On any query/schema failure every count is unavailable (null), never a fake 0.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TZ_NAME = "America/Tijuana"
TZ = ZoneInfo(TZ_NAME)

UNAVAILABLE = {
    "ok": False,
    "unavailable": True,
    "timezone": TZ_NAME,
    "sent_today": None,
    "sent_mtd": None,
    "sent_total": None,
    "pending_review": None,
    "held": None,
    "as_of": None,
    "source": None,
    "error": "unavailable",
    "note": "Hermes 8PM report (money/scans/Harvey) is independent and not shown here.",
}


def parse_sent_at(value, *, assume_utc=True) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None and assume_utc:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def sss_send_stats(c, *, now: datetime | None = None) -> dict:
    """Read-only. Fake zeros are forbidden when the query cannot run."""
    try:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        local = now.astimezone(TZ)
        today = local.date()
        month_start = today.replace(day=1)
        rows = list(c.execute("select status, sent_at from outbox"))
        sent_today = sent_mtd = sent_total = 0
        pending = held = 0
        for status, sent_at in rows:
            st = (status or "")
            if st == "pending_review":
                pending += 1
            elif st == "held":
                held += 1
            if st != "sent":
                continue
            sent_total += 1
            dt = parse_sent_at(sent_at)
            if dt is None:
                continue
            day = dt.astimezone(TZ).date()
            if day == today:
                sent_today += 1
            if day.year == today.year and day.month == today.month and month_start <= day <= today:
                sent_mtd += 1
        return {
            "ok": True,
            "unavailable": False,
            "timezone": TZ_NAME,
            "as_of": now.astimezone(TZ).isoformat(),
            "source": "harvey-sss outbox status=sent",
            "sent_today": sent_today,
            "sent_mtd": sent_mtd,
            "sent_total": sent_total,
            "pending_review": pending,
            "held": held,
            "error": None,
            "note": "Hermes 8PM report (money/scans/Harvey) is independent and not shown here.",
        }
    except Exception:
        out = dict(UNAVAILABLE)
        try:
            out["as_of"] = datetime.now(timezone.utc).astimezone(TZ).isoformat()
        except Exception:
            pass
        return out
