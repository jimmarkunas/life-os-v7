"""Fit v2 (LIFE-41): the request the named OpenClaw Jobs Fit capability gets and the code-owned envelope its answer must pass (it judges semantic match only; scoreability, caps, gate and the 72 floor stay here).
accept() returns None for an unusable answer: before cutover the static scorer may stay authoritative; after cutover an approved fail-closed or retryable path, never a silent static fallback.
Place in the title (TL review of #339, 5567054574): the job's structured location is removed and a short capitalised title tail is dropped; a place still left in the cleaned title, even with
no separator ("Senior Technical Program Manager London"), is found by the lane policy's own place patterns (`title_place`) and nothing is sent for that job (fail closed, no geo engine).
"Global" and "worldwide" are scope words, not places."""
from dataclasses import dataclass
import json
import re

from lifeos.jobs import lanes
from lifeos.jobs.fit import UNIVERSAL_FIT_FLOOR, extract
from lifeos.jobs.fit.score import JUNIOR, JUNIOR_CAP, MIN_TEXT, SENIOR

FAMILY_CAP = 40
SECTIONS = ("summary", "required", "preferred", "duty")          # what the static scorer reads; benefits, pay, place and company boilerplate are never sent
MODE = r"\b(?:remote|hybrid|on-?site|in-office)\b"
GENERIC = {"remote", "hybrid", "onsite", "office", "united", "states", "kingdom", "area", "metro", "greater", "anywhere", "worldwide"}
SUFFIX = re.compile(r",\s*[A-Z][\w.]*(?:\s[A-Z][\w.]*){0,2}$")      # a short capitalised tail such as ", Chicago" or ", Platform Programs": a place or a qualifier, indistinguishable, so dropped
KEYS = {"score", "rationale", "confidence"}
# Pay, benefits, work mode, place and visa are never Fit evidence: a clause stating one is dropped before egress (a mixed clause is lost whole; "remote teams" or "benefits realization" are kept).
POLICY = re.compile(r"[$£€]\s?\d|\b(?:salary|base pay|pay range|hourly rate|compensation (?:range|package)|competitive (?:salary|pay|compensation))\b|\b401\(?k\)?|\bpto\b|paid time off|parental leave|"
                    r"\b(?:health|medical|dental|vision) (?:insurance|coverage|plans?|benefits)\b|\bstock options\b|\bequity (?:grants?|package)\b|"
                    r"\b(?:remote|hybrid|on-?site|in-office)[- ](?:first|only|friendly|role|position|work|schedule|policy|basis|job)\b|\b(?:fully|100%|completely) remote\b|"
                    r"\bwork(?:ing)? (?:remotely|from (?:home|anywhere|the office))\b|\bon-?site\b|\bin[- ]office\b|\b\d+ days? (?:a|per) week\b|\b(?:based|located) in\b|"
                    r"\brelocat\w+|\boffice in\b|\blocation\b|\bvisa\b|\bsponsorship\b|\bauthori[sz]ed to work\b|work authori[sz]ation|\bright to work\b|\bcommut\w+|\bresid(?:e|ency|ents?)\b|\bin[- ]person\b", re.I)


# LIFE-41 10500: contact details are never sent. A clause with an email address, a phone number, a link or a contact instruction is dropped, and a title that carries one
# sends nothing.
CONTACT = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b|\+\d{1,3}(?:[\s.-]?\d{2,4}){2,4}\b|\bhttps?://|\bwww\.|"
                     r"\b(?:contact|reach out to|e-?mail|call|text|message)\s+(?:us|me|him|her|them|our|the (?:recruiter|hiring manager|team))\b|\brecruiter\b|\blinkedin\.com\b", re.I)


_PLACES = (lanes._UK, lanes._CANADA, lanes._US, lanes._OTHER, lanes.US_PLACES)
_SCOPE_WORDS = {"global", "worldwide"}


def title_place(title):
    """True when the title, as it would be sent, still names a place the lane policy knows (a UK, Canadian, US or other market name, a US city or state): such a title is
    never sent, since a place absent from the structured location cannot be removed reliably (10500: no location metadata leaves V7)."""
    return any(m.group(0).lower() not in _SCOPE_WORDS for rx in _PLACES for m in rx.finditer(title or ""))


@dataclass(frozen=True)
class Judgment:
    score: int | None
    decision: str            # Go | No-Go | Unscorable
    rationale: str
    confidence: str


def _place_words(location):
    words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", location or "")} - GENERIC
    return re.compile(r"\b(?:" + "|".join(map(re.escape, sorted(words))) + r")\b", re.I) if words else re.compile(r"(?!)")


def request_title(title, location=None):
    """The title as it would be sent: cut at a mode, place or suffix cue, the job's own location words removed, short capitalised tails dropped."""
    name = re.sub(r"(,\s*)+", ", ", _place_words(location).sub("", re.sub(MODE, "", re.split(r"\s[-–—|@]\s|[(\[]|,\s*" + MODE, title or "", flags=re.I)[0], flags=re.I))).strip(" ,")
    while SUFFIX.search(name):
        name = SUFFIX.sub("", name)
    return " ".join(name.split())


def request(title, text, profile_brief, location=None):
    """The capability's whole input (title, description, profile brief), or None when nothing substantive or safe can be sent: at least MIN_TEXT characters must remain, under a real responsibilities or
    qualifications heading (parse relabels a headingless text as requirements), with half of MIN_TEXT of it role evidence. Policy clauses and the job's own `location` words are removed and the title is cut
    at a mode, place or suffix cue and stripped of short capitalised tails; a title that cleans to nothing is ambiguous, so nothing is sent."""
    place = _place_words(location)
    kept = [(u.section, c) for u in extract.parse(text) if u.section in SECTIONS for c in re.split(r";|(?<=[.!?])\s+", u.text)
            if not POLICY.search(c) and not place.search(c) and not CONTACT.search(c)]
    headed = any(extract._heading(line.strip()) in SECTIONS[1:] for line in (text or "").split("\n"))
    short = sum(len(c) for _, c in kept) < MIN_TEXT or sum(len(c) for sec, c in kept if sec in SECTIONS[1:]) < MIN_TEXT // 2
    name = request_title(title, location)
    if short or not headed or not name.strip() or CONTACT.search(title or "") or title_place(name):
        return None
    return {"title": " ".join(name.split()), "description": "\n".join(c for _, c in kept), "profile": profile_brief}


def accept(raw, base, title, sent):
    """The capability's JSON answer under the code-owned envelope -> Judgment, or None (unusable). `sent` is what `request` returned; `base` (the static Result) supplies only the gate and the family cap, never scoreability."""
    if sent is None:
        return Judgment(None, "No-Go" if base.exclusion else "Unscorable", "No substantive description to judge.", "low")      # never a percentage
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
