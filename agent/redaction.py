"""Masks digit sequences (OTP/TAC-shaped strings) in untrusted free text
before it is classified, logged, or displayed.

Closes the known limitation docs/THREAT_MODEL.md already flagged:
"Free-text notes are not redacted... Planned mitigation: mask digit
sequences in notes before they are classified, logged or displayed; not
implemented." See that file's OWASP LLM02 (Sensitive Information
Disclosure) row for the full context.

Applied at point of USE, not at storage: the raw ShipmentEvent.notes as
submitted stays intact in the database. The append-only audit chain and
the original evidence must never be silently rewritten -- redacting at
storage time would mean the "evidence" a later dispute relies on was
quietly altered. Instead, every place that takes notes OUT of storage for
something other than exact replay (feeding the LLM classifier, writing the
agent's own reasoning text that gets audit-logged, rendering on the
dashboard) must call this first. See agent/investigator.py for the two
call sites and dashboard/views/timeline.py for a third, independent one
(the dashboard is a thin API client and deliberately does not import this
agent package, so it carries its own small copy of the same logic -- keep
both in sync if the masking rule changes).
"""
from __future__ import annotations

import re

# 4 digits is the shortest common OTP/TAC length. Shorter digit runs (a
# single digit, a two-digit count, a three-digit street number) are left
# alone -- masking those too would make ordinary notes unreadable for no
# real security benefit, since a 1-3 digit code space is not what this
# mitigation is aimed at.
_DIGIT_RUN = re.compile(r"\d{4,}")


def redact_digit_sequences(text: str | None) -> str | None:
    """Replaces every run of 4+ consecutive digits in `text` with asterisks
    of the same length (e.g. "code is 834921" -> "code is ******"). Returns
    None unchanged -- callers that already branch on "no notes" don't need
    a special case for this function too.
    """
    if text is None:
        return None
    return _DIGIT_RUN.sub(lambda m: "*" * len(m.group()), text)
