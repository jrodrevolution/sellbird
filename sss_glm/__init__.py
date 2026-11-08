# SPDX-License-Identifier: AGPL-3.0-only
"""Approval-only GLM outreach drafts via an independent Hermes runtime.

Site Safety Scan (SSS) never holds model keys. Hermes never sees recipients,
scraped page text, or send capability. Generated mail is pending_review or
held only. This package is the protocol, validation, worker, and enqueue
path for Jose's fork overlay — not a Grok Bot runtime.
"""

__all__ = ["PROTOCOL_VERSION"]

PROTOCOL_VERSION = "sss-glm-request-v1"
