# SPDX-License-Identifier: AGPL-3.0-only
"""Prompt, validator, and compose share one budget. No silent truncation."""

from __future__ import annotations

from pathlib import Path

from sss_glm.claims import BANNED_CLAIM, BANNED_REMEDIATION, FINDING_MEANING
from sss_glm.compose import (
    AUTHORITATIVE_SCAN_LINE_WORDS,
    generated_word_count,
    scan_line_for,
    strip_authoritative,
)
from sss_glm.model import (
    HERMES_KW,
    MAX_AGENT_ITERATIONS,
    MAX_COMPLETION_TOKENS,
    SYSTEM,
    system_prompt,
)
from sss_glm.protocol import (
    COMPOSED_MAX_WORDS,
    COMPOSED_MIN_WORDS,
    MAX_SUBJECT_LEN,
    MODEL_BODY_MAX_WORDS,
    MODEL_BODY_MIN_WORDS,
    word_count,
)


ROOT = Path(__file__).resolve().parents[1]


def test_one_source_of_truth_for_word_budget():
    assert MODEL_BODY_MIN_WORDS == 50
    assert MODEL_BODY_MAX_WORDS == 75
    assert AUTHORITATIVE_SCAN_LINE_WORDS == word_count(scan_line_for("example.com"))
    assert COMPOSED_MAX_WORDS == MODEL_BODY_MAX_WORDS + AUTHORITATIVE_SCAN_LINE_WORDS
    assert COMPOSED_MIN_WORDS == MODEL_BODY_MIN_WORDS
    prompt = system_prompt()
    assert f"{MODEL_BODY_MIN_WORDS} to {MODEL_BODY_MAX_WORDS} words" in prompt
    assert f"under {MAX_SUBJECT_LEN} characters" in prompt
    assert prompt == SYSTEM
    src = (ROOT / "sss_glm" / "validate.py").read_text()
    assert "MODEL_BODY_MIN_WORDS" in src and "MODEL_BODY_MAX_WORDS" in src
    assert "45 <=" not in src and "<= 70" not in src
    enq = (ROOT / "sss_glm" / "enqueue.py").read_text()
    assert "MODEL_BODY_MIN_WORDS" in enq and "generated_word_count" in enq
    assert "50 <= words <= 80" not in enq


def test_prompt_matches_every_hard_validator():
    p = SYSTEM.lower()
    assert "json only" in p
    assert "never call tools" in p
    assert "hi there" in p
    assert "do not include urls" in p
    assert "added later" in p
    assert "remediation" in p
    assert "simple" in p and "easy" in p
    assert "paid fixes" in p
    assert HERMES_KW["ephemeral_system_prompt"] == SYSTEM
    assert HERMES_KW["max_iterations"] == MAX_AGENT_ITERATIONS == 8
    assert HERMES_KW["max_tokens"] == MAX_COMPLETION_TOKENS == 1536
    assert HERMES_KW["enabled_toolsets"] == []
    assert HERMES_KW["fallback_model"] == {}
    assert HERMES_KW["skip_memory"] is True


def test_scan_line_and_footer_are_outside_generated_budget():
    generated = (
        "Hi there, I ran a free public check on example.com and noticed no DMARC "
        "record email spoofing on the public internet for patients to see too. "
        "I am only naming that one public check. You can run the same free scan "
        "in plain English when you have a minute about this observed finding "
        "without telling anyone how to change DNS records at all today."
    )
    n = word_count(generated)
    assert MODEL_BODY_MIN_WORDS <= n <= MODEL_BODY_MAX_WORDS
    composed = generated + "\n\n" + scan_line_for("example.com")
    assert generated_word_count(composed) == n
    with_footer = composed + "\n\n-- \nSite Safety Scan\n123 Example Ave"
    assert generated_word_count(with_footer) == n
    assert scan_line_for("example.com") not in strip_authoritative(with_footer)


def test_no_silent_truncation_of_model_body():
    for rel in ("sss_glm/validate.py", "sss_glm/enqueue.py", "sss_glm/compose.py",
                "sss_glm/model.py"):
        src = (ROOT / rel).read_text()
        assert "split()[: " not in src.replace(" ", "")
        assert "[:70]" not in src and "[:75]" not in src
        assert "truncate" not in src.lower()


def test_overlay_glm_path_uses_generated_budget():
    src = (ROOT / "deploy" / "harvey-sss" / "overlay" / "data" / "sss_engine.py").read_text()
    assert "generated_only=True" in src
    assert "BANNED_REMEDIATION" in src
    assert "MODEL_BODY_MIN_WORDS" in src
    write = src.split("def write_email", 1)[1].split("def check_rules", 1)[0]
    assert "_frozen_template_write_email" not in write


def test_finding_meanings_are_observations_not_fixes():
    import re
    for meaning in FINDING_MEANING.values():
        assert re.search(BANNED_REMEDIATION, meaning, re.I) is None
        assert re.search(BANNED_CLAIM, meaning, re.I) is None
