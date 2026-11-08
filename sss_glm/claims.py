# SPDX-License-Identifier: AGPL-3.0-only
"""Allowlisted findings and the only claims a GLM draft may make.

Finding codes and their meanings are operator-authored, not website text.
The model may paraphrase these meanings; it may not invent other issues,
prices, urgency, or results. Recipient identity, the scan URL, footer and
unsubscribe are composed outside the model.
"""

from __future__ import annotations

# Exact check names used by the production SSS engine (sss_engine.PRIORITY).
FINDING_HTTP = "HTTP \u2192 HTTPS redirect"

FINDINGS = (
    "PHP version",
    "readme.html",
    "xmlrpc.php",
    "SPF",
    "DMARC",
    FINDING_HTTP,
)

CATEGORIES = ("dental", "law", "accounting")

AUDIENCE = {
    "dental": "patients",
    "law": "clients",
    "accounting": "clients",
}

# Trusted, short meaning. Copied from the production LABEL table.
FINDING_MEANING = {
    "PHP version": "older PHP version visible in server header",
    "readme.html": "/readme.html is public (shows WordPress details)",
    "xmlrpc.php": "/xmlrpc.php answers publicly",
    "SPF": "no SPF record (email deliverability)",
    "DMARC": "no DMARC record (email spoofing)",
    FINDING_HTTP: "http:// version doesn't redirect to https",
}

# Tokens that must appear in the model body for that finding (case-insensitive).
REQUIRED_TOKENS = {
    "PHP version": ("php",),
    "readme.html": ("readme.html",),
    "xmlrpc.php": ("xmlrpc",),
    "SPF": ("spf",),
    "DMARC": ("dmarc",),
    FINDING_HTTP: ("http", "https"),
}

# Mentioning a finding that was not in the request is an invented claim.
FOREIGN_FINDING_MARKERS = {
    "PHP version": ("php",),
    "readme.html": ("readme.html",),
    "xmlrpc.php": ("xmlrpc",),
    "SPF": ("spf",),
    "DMARC": ("dmarc",),
    FINDING_HTTP: (),  # http/https appear in other copy; gated separately
}

# Free emails name the public finding only. Paid-fix leakage is a hard fail.
BANNED_CLAIM = (
    r"\b(safe|hackproof|hack-proof|guarantee[sd]?|hacked|urgent|act now|"
    r"immediately|malware|ransomware|breach(?:ed)?|stolen|compromised|"
    r"full report|fix report|paid|invoice|wire|bitcoin|crypto)\b"
    r"|\$\s*\d+|\b29\b|\b9/month\b|\b9 per month\b"
)

BANNED_REPLY_SUBJECT = r"(?i)^\s*(re|fwd|fw)\s*:"
URL_IN_MODEL = r"https?://|\bwww\."
EMAIL_IN_MODEL = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
PHONE_IN_MODEL = r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
PLACEHOLDER = r"\{\{|\}\}|\bNone\b|\{[a-z_]+\}"
