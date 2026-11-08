# SPDX-License-Identifier: AGPL-3.0-only
"""Assemble the outgoing draft with identity, link, footer and unsub outside the model.

The model never chooses the recipient, the scan URL, the signature, the
CAN-SPAM footer or the unsubscribe link. Greeting uses a verified first
name only when the caller already decided it is safe; otherwise 'Hi there,'.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sss_glm.protocol import ProtocolError

SCAN_ORIGIN = "https://sitesafetyscan.com"
SCAN_QUERY = SCAN_ORIGIN + "/?url="
# Sign-off, CAN-SPAM footer and unsubscribe are added by finalize() outside
# the model (production: harvey.unsub.render + CANSPAM_FOOTER).


@dataclass(frozen=True)
class Composed:
    subject: str
    body: str
    scan_url: str


def scan_url_for(domain: str) -> str:
    return SCAN_QUERY + domain


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
    url = scan_url_for(domain)
    text = body.strip()
    # Authoritative greeting: replace a leading Hi there, if present.
    hi = greeting(first)
    text = re.sub(r"^Hi there,\s*", hi + " ", text, count=1)
    if not text.startswith("Hi "):
        text = hi + " " + text
    if url not in text:
        text = text.rstrip() + f"\n\nThe free results are at {url}"
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
