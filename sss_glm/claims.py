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

# DIY / how-to hints. Observation text in FINDING_MEANING must not match.
# "no DMARC record" is allowed; "Publishing a DMARC record is a simple DNS
# change" is held. Risk-only outreach: name the finding, invite a free check.
BANNED_REMEDIATION = (
    r"\b(?:publish(?:ing)?|add(?:ing)?(?:\s+a)?|create(?:ing)?(?:\s+a)?|"
    r"enabl(?:e|ing)|set\s*up|configur(?:e|ing)|install(?:ing)?|"
    r"upgrad(?:e|ing)|remov(?:e|ing)|disabl(?:e|ing)|delet(?:e|ing))\b"
    r".{0,48}\b(?:dmarc|spf|dkim|dns|xmlrpc|readme\.html|php)\b"
    r"|"
    r"\b(?:simple|easy|quick)\s+(?:dns\s+)?(?:change|fix|update)\b"
    r"|"
    r"\bdns\s+change\b"
    r"|"
    r"\btxt\s+record\b"
    r"|"
    r"\b(?:you\s+should|you\s+can\s+just|just)\s+"
    r"(?:add|publish|set|enable|fix|update|upgrade|remove|disable|redirect|configure)\b"
    r"|"
    r"\bhow\s+to\s+(?:fix|add|publish|enable|set)\b"
    r"|"
    r"\bfix\s+(?:it|this|that)\s+by\b"
    r"|"
    r"\bredirect\s+(?:http|your\s+site|visitors)\b"
)

BANNED_REPLY_SUBJECT = r"(?i)^\s*(re|fwd|fw)\s*:"
URL_IN_MODEL = r"https?://|\bwww\."
EMAIL_IN_MODEL = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
PHONE_IN_MODEL = r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
PLACEHOLDER = r"\{\{|\}\}|\bNone\b|\{[a-z_]+\}"
