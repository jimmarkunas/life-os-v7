"""Step 0: hard exclusions. Generic defaults (lexicon) plus the private profile's own rules.

A hit is HARD (score 0, stop) when it is in the title or company, in the first 600 characters of the posting, in a
`strict` rule (security clearance), or repeated (2+ mentions). A single deep mention is only noted as soft: a
consultancy listing "healthcare" among ten client industries is not a healthcare role.
"""
import re

from lifeos.jobs.fit import lexicon
from lifeos.jobs.fit.profile import norm, term_regex

LEAD = 600


def _matcher(rule):
    term_rx = [term_regex(t) for t in rule.get("terms") or []]
    pattern_rx = [re.compile(p, re.I) for p in rule.get("patterns") or []]

    def positions(raw, normed):
        spots = [m.start() for rx in term_rx for m in rx.finditer(normed)]
        spots += [m.start() for rx in pattern_rx for m in rx.finditer(raw)]
        return spots
    return positions


def check(title, company, text, profile=None):
    """-> (hard, soft): hard is {"id","reason","where"} or None; soft is a list of rule ids seen once, deep in the text."""
    rules = [*lexicon.DEFAULT_EXCLUSIONS, *(profile.exclusions if profile else [])]
    raw_text = text or ""
    soft = []
    for rule in rules:
        positions = _matcher(rule)
        where = rule.get("where") or "any"
        if where in ("title", "any") and positions(title or "", norm(title)):
            return {"id": rule["id"], "reason": rule.get("reason") or rule["id"], "where": "title"}, soft
        if where in ("company", "any") and positions(company or "", norm(company)):
            return {"id": rule["id"], "reason": rule.get("reason") or rule["id"], "where": "company"}, soft
        if where != "any":
            continue
        spots = positions(raw_text.lower(), norm(raw_text))
        if not spots:
            continue
        # offsets differ between raw and normalized text, so judge "early" on the raw-text pattern hits alone
        early = any(m.start() < LEAD for m in _early(rule, raw_text)) and not rule.get("min_hits")
        if rule.get("strict") or early or len(spots) >= rule.get("min_hits", 2):
            return {"id": rule["id"], "reason": rule.get("reason") or rule["id"], "where": "text"}, soft
        soft.append(rule["id"])
    return None, soft


def _early(rule, raw_text):
    head = raw_text[:LEAD]
    head_norm = norm(head)
    out = []
    for t in rule.get("terms") or []:
        out += list(term_regex(t).finditer(head_norm))
    for p in rule.get("patterns") or []:
        out += list(re.finditer(p, head, re.I))
    return out
