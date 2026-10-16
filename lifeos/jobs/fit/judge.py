"""Fit v2 (LIFE-41): what the named OpenClaw Jobs Fit capability is asked, and what code accepts back from it.

The capability judges semantic professional match only. Scoreability, the hard-family and level caps, the exclusion gate and the universal floor stay code-owned here. An unusable
answer is refused (None) and nothing is invented. Before cutover the caller may leave the static scorer authoritative on None; after cutover None must follow an explicitly approved
fail-closed or retryable path, never a silent static fallback. No provider logic, no fallback model, no persistence.
"""
from dataclasses import dataclass
import json
import re

from lifeos.jobs.fit import UNIVERSAL_FIT_FLOOR, extract
from lifeos.jobs.fit.score import JUNIOR, JUNIOR_CAP, MIN_TEXT, SENIOR

FAMILY_CAP = 40
SECTIONS = ("summary", "required", "preferred", "duty")          # what the static scorer reads; benefits, pay, place and company boilerplate are never sent
KEYS = {"score", "rationale", "confidence"}
# Pay, benefits, work mode, place and visa are never Fit evidence: a clause stating one is dropped before egress (a mixed clause is lost whole; "remote teams" or "benefits realization" are kept).
POLICY = re.compile(r"[$£€]\s?\d|\b(?:salary|base pay|pay range|hourly rate|compensation (?:range|package)|competitive (?:salary|pay|compensation))\b|\b401\(?k\)?|\bpto\b|paid time off|parental leave|"
                    r"\b(?:health|medical|dental|vision) (?:insurance|coverage|plans?|benefits)\b|\bstock options\b|\bequity (?:grants?|package)\b|"
                    r"\b(?:remote|hybrid|on-?site|in-office)[- ](?:first|only|friendly|role|position|work|schedule|policy|basis|job)\b|\b(?:fully|100%|completely) remote\b|"
                    r"\bwork(?:ing)? (?:remotely|from (?:home|anywhere|the office))\b|\bon-?site\b|\bin[- ]office\b|\b\d+ days? (?:a|per) week\b|\b(?:based|located) in\b|"
                    r"\brelocat\w+|\boffice in\b|\blocation\b|\bvisa\b|\bsponsorship\b|\bauthori[sz]ed to work\b|work authori[sz]ation|\bright to work\b", re.I)


@dataclass(frozen=True)
class Judgment:
    score: int | None
    decision: str            # Go | No-Go | Unscorable
    rationale: str
    confidence: str


def request(title, text, profile_brief):
    """The capability's whole input: the title, the substantive description and the canonical profile brief; None (nothing is sent) when less than MIN_TEXT characters of description
    remain. Pay, benefits, work mode, place and visa statements are removed clause by clause wherever they appear; source and freshness are never in the text."""
    clauses = [c for u in extract.parse(text) if u.section in SECTIONS for c in re.split(r";|(?<=[.!?])\s+", u.text) if not POLICY.search(c)]
    description = "\n".join(clauses)
    return {"title": title, "description": description, "profile": profile_brief} if len(description) >= MIN_TEXT else None


def accept(raw, base, title, sent):
    """The capability's JSON answer under the code-owned envelope -> Judgment, or None (unusable). `sent` is what `request` returned for the posting; `base` is the static `evaluate` Result,
    used only for the exclusion gate and the hard-family cap, never for scoreability: the static scorer failing to recognise a requirement does not make a substantive description unscorable."""
    if sent is None:
        return Judgment(None, "No-Go" if base.exclusion else "Unscorable", "No substantive description to judge.", "low")      # title-only or too little description: never a percentage
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
