# SPDX-License-Identifier: AGPL-3.0-only
"""Assemble the outgoing draft with identity, link, footer and unsub outside the model.

The model never chooses the recipient, the scan URL, the signature, the
CAN-SPAM footer or the unsubscribe link. Greeting uses a verified first
name only when the caller already decided it is safe; otherwise 'Hi there,'.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sss_glm.protocol import (
    AUTHORITATIVE_SCAN_LINE_WORDS,
    ProtocolError,
    word_count,
)

SCAN_ORIGIN = "https://sitesafetyscan.com"
SCAN_QUERY = SCAN_ORIGIN + "/?url="
SCAN_LINE_PREFIX = "The free results are at "
# Sign-off, CAN-SPAM footer and unsubscribe are added by finalize() outside
# the model (production: harvey.unsub.render + CANSPAM_FOOTER).
_SCAN_LINE_TAIL = re.compile(
    r"\n\nThe free results are at https://sitesafetyscan\.com/\?url=\S+\s*$"
)


@dataclass(frozen=True)
class Composed:
    subject: str
    body: str
    scan_url: str


def scan_url_for(domain: str) -> str:
    return SCAN_QUERY + domain


def scan_line_for(domain: str) -> str:
    return SCAN_LINE_PREFIX + scan_url_for(domain)


def strip_authoritative(body: str) -> str:
    """Drop footer and the composed scan line. Generated copy remains intact."""
    text = (body or "").split("\n\n--")[0].rstrip()
    return _SCAN_LINE_TAIL.sub("", text).rstrip()


def generated_word_count(body: str) -> int:
    return word_count(strip_authoritative(body))


def assert_scan_line_budget() -> None:
    n = word_count(scan_line_for("example.com"))
    if n != AUTHORITATIVE_SCAN_LINE_WORDS:
        raise ProtocolError("compose_budget", "scan_line")


def greeting(first: str | None) -> str:
    name = (first or "").strip()
    if not name or not re.fullmatch(r"(?:Dr\. )?[A-Za-z][A-Za-z'\-]{0,30}", name):
        return "Hi there,"
    return f"Hi {name},"


def compose(*, domain: str, first: str | None, subject: str, body: str,
            finalize=None, prospect_id: str = "") -> Composed:
    """Wrap a validated model subject/body with authoritative identity lines.

    ``finalize(body, prospect_id)`` adds the production footer + unsub. Tests
    inject a stub. Production passes harvey.unsub.render.
    """
    if "://" in domain or "/" in domain or " " in domain:
        raise ProtocolError("domain")
    assert_scan_line_budget()
    url = scan_url_for(domain)
    text = body.strip()
    # Authoritative greeting: replace a leading Hi there, if present.
    hi = greeting(first)
    text = re.sub(r"^Hi there,\s*", hi + " ", text, count=1)
    if not text.startswith("Hi "):
        text = hi + " " + text
    if url not in text:
        text = text.rstrip() + "\n\n" + scan_line_for(domain)
    extra = re.findall(r"https?://[^\s]+", text)
    allowed = {url}
    if any(link.rstrip(").,") not in allowed for link in extra):
        raise ProtocolError("invented_claim", "url")
    signed = text.rstrip()
    if finalize is not None:
        signed = finalize(signed, prospect_id)
    if SCAN_ORIGIN not in signed:
        raise ProtocolError("mismatch", "scan_link")
    return Composed(subject=subject, body=signed, scan_url=url)
