"""Fit v2 (LIFE-41): what the named OpenClaw Jobs Fit capability is asked, and what code accepts back from it.

The capability judges semantic professional match only. Scoreability, the hard-family and level caps, the exclusion gate and the universal floor stay code-owned here. An unusable
answer is refused (None) and nothing is invented. Before cutover the caller may leave the static scorer authoritative on None; after cutover None must follow an explicitly approved
fail-closed or retryable path, never a silent static fallback. No provider logic, no fallback model, no persistence.
"""
from dataclasses import dataclass
import json

from lifeos.jobs.fit import UNIVERSAL_FIT_FLOOR, extract
from lifeos.jobs.fit.score import JUNIOR, JUNIOR_CAP, SENIOR

FAMILY_CAP = 40
SECTIONS = ("summary", "required", "preferred", "duty")          # what the static scorer reads; benefits, pay, place and company boilerplate are never sent
KEYS = {"score", "rationale", "confidence"}


@dataclass(frozen=True)
class Judgment:
    score: int | None
    decision: str            # Go | No-Go | Unscorable
    rationale: str
    confidence: str


def request(title, text, profile_brief):
    """The capability's whole input: the title, the substantive description and the canonical profile brief. Location, work mode, pay, source and freshness are not in it."""
    return {"title": title, "description": "\n".join(u.text for u in extract.parse(text) if u.section in SECTIONS), "profile": profile_brief}


def accept(raw, base, title):
    """The capability's JSON answer under the code-owned envelope -> Judgment, or None (unusable). `base` is the static `evaluate` Result for the same posting: it owns scoreability and the gate."""
    if base.score is None:
        return Judgment(None, base.decision, base.why, "low")      # title-only or requirement-free: unscorable stays unscorable, never a percentage
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        score, rationale, confidence = data["score"], data["rationale"], data["confidence"]
    except (ValueError, TypeError, KeyError):
        return None
    if set(data) != KEYS or type(score) is not int or not 0 <= score <= 100 or not isinstance(rationale, str) or confidence not in ("low", "medium", "high"):
        return None
    if base.capped:
        score = min(score, FAMILY_CAP)                             # a hard-family mismatch is a code rule, not a judgment
    if JUNIOR.search(title or "") and not SENIOR.search(title or ""):
        score = min(score, JUNIOR_CAP)
    return Judgment(score, "No-Go" if base.exclusion or score < UNIVERSAL_FIT_FLOOR else "Go", " ".join(rationale.split())[:300], confidence)
