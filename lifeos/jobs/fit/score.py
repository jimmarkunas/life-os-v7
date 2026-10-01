"""The fit arithmetic (docs/FIT_MODEL.md): four weighted buckets, each requirement classified against the injected
private profile, committed corrections always Direct, inflated or optional requirements carry half the penalty.

  bucket  = 1 - sum(w * penalty * (1 - value)) / sum(w)        (w = importance: the title counts half)
  total   = sum(bucket_weight * bucket) / sum(weights of the buckets the posting actually exercises)
"""
from dataclasses import dataclass, field
import re

from lifeos.jobs.fit import GO_THRESHOLD, MODEL_VERSION, exclusions, extract, lexicon
from lifeos.jobs.fit.profile import VALUE, norm

WEIGHTS = {"required": 0.40, "platform": 0.25, "role": 0.20, "seniority": 0.15}
NAMES = {"required": "Required Skills", "platform": "Platform/Stack", "role": "Role/Title", "seniority": "Seniority/Scope"}
MIN_TEXT = 300
_PLATFORM_KEYS = {t for t in lexicon.PLATFORMS}
_PHD = re.compile(r"\b(?:phd|ph\.d|doctorate)\b", re.I)


@dataclass
class Item:
    bucket: str
    label: str
    cls: str
    penalty: float = 1.0
    weight: float = 1.0
    strength: str = ""


@dataclass
class Result:
    score: int | None
    decision: str                      # Go | No-Go | No-Data
    line: str
    why: str
    exclusion: str | None = None
    confidence: str = "low"
    buckets: dict = field(default_factory=dict)
    items: list = field(default_factory=list)
    soft: list = field(default_factory=list)


def _half_up(x):
    return int(x + 0.5)


def _inflated(unit, today_year):
    text = unit.norm
    if _PHD.search(unit.text) and not re.search(r"or equivalent|preferred", unit.text, re.I):
        return True
    for m in extract._YEARS.finditer(unit.text):
        years = int(m.group(1))
        if years >= 15:
            return True
        for term, first in lexicon.TECH_FIRST_YEAR.items():
            if f" {term} " in text and years > today_year - first + 1:
                return True
    return False


def _best(profile, text):
    cap, fn = profile.capability(text), profile.function(text)
    pick = [x for x in (cap, fn) if x]
    return max(pick, key=lambda r: VALUE[r["class"]]) if pick else None


def _short(text, n=70):
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _required(units, profile, year):
    items = []
    for u in extract.requirement_units(units):
        text = u.norm
        hits = sum(1 for _, rx in extract._PLATFORM_RX if rx.search(text))
        if hits >= 2 and len(u.text) < 140 or hits and len(u.text.split()) <= 8:
            continue                                                  # a stack list or one named tool: Platform owns it
        row = _best(profile, text)
        if not row and extract.scope_hits([u]):
            continue                                                  # team size, budget, reach: Seniority owns it
        penalty = 0.5 if u.optional or _inflated(u, year) else 1.0
        if row:
            items.append(Item("required", _short(u.text), row["class"], penalty, strength=row["label"]))
        elif profile.baseline and any(b in text for b in lexicon.BASELINE):
            items.append(Item("required", _short(u.text), "direct", penalty))
        else:
            items.append(Item("required", _short(u.text), "unsupported", penalty))
    return items


def _platform(units, profile):
    found = {}                                                        # key -> Item (required beats optional)

    def add(key, label, row, optional):
        penalty = 0.5 if optional else 1.0
        cls = row["class"] if row else "unsupported"
        item = Item("platform", label, cls, penalty, strength=row["label"] if row else "")
        if key not in found or penalty > found[key].penalty:
            found[key] = item

    for term, optional in extract.platform_hits(units):
        row = profile.capability(norm(term))
        add(row["id"] if row else term, term, row, optional)
    body = [u for u in units if u.section in ("required", "preferred", "duty")]
    for cap in profile.capabilities:
        if cap["bucket"] != "platform" or cap["id"] in found:
            continue
        hit = [u for u in body if any(rx.search(u.norm) for rx in cap["rx"])]
        if hit:
            add(cap["id"], cap["label"], cap, all(u.optional or u.section == "duty" for u in hit))
    for name, optional in extract.candidates(units, _PLATFORM_KEYS):
        row = profile.capability(norm(name))
        add(row["id"] if row else norm(name).strip(), name, row, optional)
    items = list(found.values())
    for item in items[6:] if len(items) > 12 else []:                  # 15 platforms listed, ~5 are core
        item.penalty = min(item.penalty, 0.5)
    return items


def _role(title, units, profile):
    items = []
    fn = profile.function(norm(title)) or profile.capability(norm(title))
    off = next((t for t in lexicon.OFF_TARGET if t in (title or "").lower()), None)
    if fn:
        items.append(Item("role", _short(title), fn["class"], 1.0, 0.5, fn["label"]))
    elif off:
        items.append(Item("role", _short(title), "unsupported", 1.0, 0.5))
    for u in extract.duty_units(units):
        row = _best(profile, u.norm)
        if row:
            items.append(Item("role", _short(u.text), row["class"], 1.0, 1.0, row["label"]))
        elif any(t in u.text.lower() for t in lexicon.OFF_TARGET):
            items.append(Item("role", _short(u.text), "unsupported"))
    return items


def _seniority(units, profile, year):
    items = []
    yrs = extract.max_years(units)
    if yrs and profile.years:
        n, unit = yrs
        cls = "direct" if profile.years >= n else "adjacent" if profile.years >= 0.75 * n else "method"
        items.append(Item("seniority", f"{n}+ years", cls, 0.5 if _inflated(unit, year) else 1.0, strength=f"{profile.years}+ years"))
    for name, found in extract.scope_hits([u for u in units if u.section != "preferred"]).items():
        mine = profile.scope.get({"team_size": "team_size", "budget": "budget_usd"}.get(name, name))
        if isinstance(found, bool):
            cls = mine if mine in VALUE else "unsupported" if not mine else "direct"
        elif mine is None or isinstance(mine, str):
            cls = mine if mine in VALUE else "unsupported"
        else:
            cls = "direct" if mine >= found else "adjacent" if mine >= 0.5 * found else "method"
        items.append(Item("seniority", name.replace("_", " "), cls, 1.0, strength=name.replace("_", " ")))
    return items


def _bucket_value(items):
    total = sum(i.weight for i in items)
    loss = sum(i.weight * i.penalty * (1 - VALUE[i.cls]) for i in items)
    return 1 - loss / total


def _line(score, decision, items, tail=None):
    strengths = list(dict.fromkeys(i.strength or i.label for i in items if i.cls == "direct" and i.strength))[:3]
    gaps = list(dict.fromkeys(i.label for i in sorted(items, key=lambda i: -i.penalty) if i.cls == "unsupported"))[:3]
    return (f"[{score}%] {decision} | Strengths: {', '.join(strengths) or 'none evidenced'} | "
            f"Gaps: {', '.join(gaps) or 'none'}") + (f" | {tail}" if tail else "")


def evaluate(title, company, text, profile, today):
    """Score one posting. `text` is the full job description text; `today` a date (inflation uses its year)."""
    text = text or ""
    hard, soft = exclusions.check(title, company, text, profile)
    if hard:
        why = f"Hard exclusion: {hard['reason']} ({hard['where']})."
        return Result(0, "No-Go", f"[0%] No-Go | Excluded: {hard['reason']}", why, exclusion=hard["id"], confidence="high", soft=soft)
    if len(text) < MIN_TEXT:
        return Result(None, "No-Data", "[--] No-Data | description too short to score", "Description under 300 characters.")
    units = extract.parse(text)
    items = {
        "required": _required(units, profile, today.year), "platform": _platform(units, profile),
        "role": _role(title, units, profile), "seniority": _seniority(units, profile, today.year),
    }
    live = {b: i for b, i in items.items() if i}
    if not live:
        return Result(None, "No-Data", "[--] No-Data | no scorable requirements", "No requirement was recognised.")
    values = {b: _bucket_value(i) for b, i in live.items()}
    total = sum(WEIGHTS[b] * v for b, v in values.items()) / sum(WEIGHTS[b] for b in values)
    score = _half_up(total * 100)
    decision = "Go" if score >= GO_THRESHOLD else "No-Go"
    flat = [i for b in WEIGHTS for i in items.get(b, [])]
    weakest = min(values, key=values.get)
    why = (f"Meets the {GO_THRESHOLD}% bar." if decision == "Go"
           else f"Below {GO_THRESHOLD}%: weakest area is {NAMES[weakest]} ({_half_up(values[weakest] * 100)}%).")
    confident = len(items["required"]) >= 3 and len(flat) >= 8 and len(text) >= 1200 and len(values) >= 3
    return Result(score, decision, _line(score, decision, flat), why, confidence="high" if confident else "low",
                  buckets={b: {"score": _half_up(v * 100), "n": len(live[b])} for b, v in values.items()},
                  items=[[i.bucket, i.label, i.cls, i.penalty] for i in flat][:80], soft=soft)
