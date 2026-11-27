"""Fit v2: the request the OpenClaw Jobs Fit capability gets and the code-owned envelope its answer must pass.

The capability judges semantic match only; scoreability, caps, gate and the 72 floor stay here. accept() returns
None for an unusable answer. The job's structured location is removed from the title and a short capitalised tail
dropped; a place still left is caught by the lane policy's place patterns (`title_place`) and nothing is sent
(fail closed).
"""

from dataclasses import dataclass
import json
import re

from lifeos.jobs import lanes
from lifeos.jobs.fit import UNIVERSAL_FIT_FLOOR, extract
from lifeos.jobs.fit.score import JUNIOR, JUNIOR_CAP, MIN_TEXT, SENIOR

FAMILY_CAP = 40
SECTIONS = ("summary", "required", "preferred", "duty")
MODE = r"\b(?:remote|hybrid|on-?site|in-office)\b"
GENERIC = {
    "remote",
    "hybrid",
    "onsite",
    "office",
    "united",
    "states",
    "kingdom",
    "area",
    "metro",
    "greater",
    "anywhere",
    "worldwide",
}
SUFFIX = re.compile(r",\s*[A-Z][\w.]*(?:\s[A-Z][\w.]*){0,2}$")
KEYS = {"score", "rationale", "confidence"}
# Pay, benefits, work mode, place and visa are never Fit evidence: a clause stating one is dropped before egress (a
# mixed clause is lost whole).
POLICY = re.compile(
    r"[$£€]\s?\d|\b(?:salary|base pay|pay range|hourly rate|compensation (?:range|package)|competitive (?:salary|pay|compensation))\b|\b401\(?k\)?|\bpto\b|paid time off|parental leave|"
    r"\b(?:health|medical|dental|vision) (?:insurance|coverage|plans?|benefits)\b|\bstock options\b|\bequity (?:grants?|package)\b|"
    r"\b(?:remote|hybrid|on-?site|in-office)[- ](?:first|only|friendly|role|position|work|schedule|policy|basis|job)\b|\b(?:fully|100%|completely) remote\b|"
    r"\bwork(?:ing)? (?:remotely|from (?:home|anywhere|the office))\b|\bon-?site\b|\bin[- ]office\b|\b\d+ days? (?:a|per) week\b|\b(?:based|located) in\b|"
    r"\brelocat\w+|\boffice in\b|\blocation\b|\bvisa\b|\bsponsorship\b|\bauthori[sz]ed to work\b|work authori[sz]ation|\bright to work\b|\bcommut\w+|\bresid(?:e|ency|ents?)\b|\bin[- ]person\b",
    re.I,
)


# Contact details are never sent: a clause with an email address, phone number, link or contact instruction is
# dropped, and a title carrying one sends nothing.
CONTACT = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b|\+\d{1,3}(?:[\s.-]?\d{2,4}){2,4}\b|\bhttps?://|\bwww\.|"
    r"\b(?:contact|reach out to|e-?mail|call|text|message)\s+(?:us|me|him|her|them|our|the (?:recruiter|hiring manager|team))\b|\brecruiter\b|\blinkedin\.com\b",
    re.I,
)


_PLACES = (lanes._UK, lanes._CANADA, lanes._US, lanes._OTHER, lanes.US_PLACES)
_SCOPE_WORDS = {"global", "worldwide"}


def title_place(title):
    """True when the title, as it would be sent, still names a place the lane policy knows: such a title is never
    sent.
    """
    return any(m.group(0).lower() not in _SCOPE_WORDS for rx in _PLACES for m in rx.finditer(title or ""))


@dataclass(frozen=True)
class Judgment:
    score: int | None
    decision: str
    rationale: str
    confidence: str


def _place_words(location):
    words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", location or "")} - GENERIC
    return (
        re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(words))) + r")\b", re.I) if words else re.compile(r"(?!)")
    )


def request_title(title, location=None):
    name = re.sub(
        r"(,\s*)+",
        ", ",
        _place_words(location).sub(
            "", re.sub(MODE, "", re.split(r"\s[-–—|@]\s|[(\[]|,\s*" + MODE, title or "", flags=re.I)[0], flags=re.I)
        ),
    ).strip(" ,")
    while SUFFIX.search(name):
        name = SUFFIX.sub("", name)
    return " ".join(name.split())


def request(title, text, profile_brief, location=None):
    """The capability's whole input (title, description, profile brief), or None when nothing substantive or safe can
    be sent: at least MIN_TEXT characters must remain under a real responsibilities or qualifications heading,
    half of them role evidence. Policy clauses and the job's `location` words are removed.
    """
    place = _place_words(location)
    kept = [
        (u.section, c)
        for u in extract.parse(text)
        if u.section in SECTIONS
        for c in re.split(r";|(?<=[.!?])\s+", u.text)
        if not POLICY.search(c) and not place.search(c) and not CONTACT.search(c)
    ]
    headed = any(extract._heading(line.strip()) in SECTIONS[1:] for line in (text or "").split("\n"))
    short = (
        sum(len(c) for _, c in kept) < MIN_TEXT or sum(len(c) for sec, c in kept if sec in SECTIONS[1:]) < MIN_TEXT // 2
    )
    name = request_title(title, location)
    if short or not headed or not name.strip() or CONTACT.search(title or "") or title_place(name):
        return None
    return {"title": " ".join(name.split()), "description": "\n".join(c for _, c in kept), "profile": profile_brief}


def accept(raw, base, title, sent):
    """The capability's JSON answer under the code-owned envelope -> Judgment, or None (unusable). `base` (the static
    Result) supplies only the gate and the family cap.
    """
    if sent is None:
        return Judgment(
            None, "No-Go" if base.exclusion else "Unscorable", "No substantive description to judge.", "low"
        )
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        score, rationale, confidence = data["score"], data["rationale"], data["confidence"]
    except (ValueError, TypeError, KeyError):
        return None
    if (
        set(data) != KEYS
        or type(score) is not int
        or not 0 <= score <= 100
        or not isinstance(rationale, str)
        or confidence not in ("low", "medium", "high")
    ):
        return None
    if base.capped:
        score = min(score, FAMILY_CAP)
    if JUNIOR.search(title or "") and not SENIOR.search(title or ""):
        score = min(score, JUNIOR_CAP)
    return Judgment(
        score,
        "No-Go" if base.exclusion or score < UNIVERSAL_FIT_FLOOR else "Go",
        " ".join(rationale.split())[:300],
        confidence,
    )
