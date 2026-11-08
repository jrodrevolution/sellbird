# SPDX-License-Identifier: AGPL-3.0-only
"""Fence untrusted structured facts the same way Sellbird fences website text."""

from __future__ import annotations

import re
import unicodedata

_TAG_RE = re.compile(
    r"(?:<|&lt;|&amp;lt;)\s*/?\s*untrusted\b(?:[^>&]|&(?!gt;|amp;gt;))*(?:>|&gt;|&amp;gt;)?",
    re.IGNORECASE)
_ZERO_WIDTH_RE = re.compile("[\u200b-\u200f\u2060\ufeff]")


def fence(text, source: str) -> str:
    clean = _ZERO_WIDTH_RE.sub("", unicodedata.normalize("NFKC", str(text or "")))
    clean = _TAG_RE.sub("[tag removed]", clean)
    label = re.sub(r"[^\w ,.-]", "", source)[:40]
    return f'<untrusted source="{label}">\n{clean}\n</untrusted>'
