"""Professional Fit, V3 (docs/FIT_MODEL.md): five weighted dimensions over the requirements the posting activates.

  Role/seniority 29 (title evidence is a separate channel worth 29/4 of it) | Functional 29 | Technical/platform 21 |
  Delivery complexity 14 | Competitive advantage 7.  Every requirement belongs to exactly one dimension.
  Evidence: Direct 1, Adjacent 3/4, Method-equivalent 3/5, Unsupported 0. REQUIRED counts double, an inflated
  requirement half. A dimension the posting does not activate is not applicable (it neither helps nor hurts).
  A Direct title specialization adds 3. Fit is capped at 40 only when the posting's actual profession is a hard-family
  mismatch. Title-only or requirement-free postings are UNSCORABLE, never 0.
Fit never sees location, work mode, pay, visa, provider score, freshness. Exclusions are a separate gate (exclusions.py).
"""
from dataclasses import dataclass, field
from fractions import Fraction
import re

from lifeos.jobs.fit import GO_THRESHOLD, exclusions, extract, lexicon
from lifeos.jobs.fit.profile import VALUE, norm

BUDGET = {"role": Fraction(29), "functional": Fraction(29), "technical": Fraction(21), "delivery": Fraction(14),
          "advantage": Fraction(7)}
NAMES = {"role": "Role/Seniority", "functional": "Functional", "technical": "Technical/Platform",
         "delivery": "Delivery complexity", "advantage": "Competitive advantage"}
TITLE_SHARE = Fraction(29, 4)
EVIDENCE = {k: Fraction(v).limit_denominator(20) for k, v in VALUE.items()}
MIN_TEXT = 300
FAMILY_SHARE = 0.5
_ROLE, _TECH, _DELIVERY = (re.compile(p, re.I) for p in (lexicon.DIM_ROLE, lexicon.DIM_TECH, lexicon.DIM_DELIVERY))
_REQUIRED = re.compile(r"\b(required|must(?:-have)?|minimum|mandatory)\b|\bat least \d+ years?\b", re.I)
_PHD = re.compile(r"\b(?:phd|ph\.d|doctorate)\b", re.I)
_PLATFORM_KEYS = set(lexicon.PLATFORMS)


@dataclass
class Req:
    dim: str
    label: str
    cls: str
    weight: Fraction = Fraction(1)
    strength: str = ""
    sim: float = 0.0
    anchor: bool = False               # matched a profile capability or function (not a baseline phrase or a bare years/scope count)


@dataclass
class Result:
    score: int | None
    decision: str                      # Go | No-Go | Unscorable
    line: str
    why: str
    exclusion: str | None = None       # the gate that excluded it (separate from the Fit number)
    confidence: str = "low"
    capped: bool = False
    buckets: dict = field(default_factory=dict)
    items: list = field(default_factory=list)
    soft: list = field(default_factory=list)
    shadow_score: int | None = None    # the score if the semantic layer's matches were applied (shadow: never used)
    shadow_changes: int = 0            # requirements the semantic layer would reclassify
    shadow_flip: bool = False          # would the Go / No-Go decision differ
    shadow_sims: list = field(default_factory=list)


def _half_up(x):
    return int(x + Fraction(1, 2))


def _short(text, n=70):
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _inflated(unit, year):
    text = unit.norm
    if _PHD.search(unit.text) and not re.search(r"or equivalent|preferred", unit.text, re.I):
        return True
    for m in extract._YEARS.finditer(unit.text):
        years = int(m.group(1))
        if years >= 15 or any(f" {t} " in text and years > year - first + 1 for t, first in lexicon.TECH_FIRST_YEAR.items()):
            return True
    return False


def _weight(unit, year):
    w = Fraction(2) if unit.strict or _REQUIRED.search(unit.text) else Fraction(1)
    return w / 2 if _inflated(unit, year) else w


def _best(profile, text):
    rows = [r for r in (profile.capability(text), profile.function(text)) if r]
    return max(rows, key=lambda r: VALUE[r["class"]]) if rows else None


def _dimension(unit, profile):
    text = unit.text
    if _ROLE.search(text):
        return "role"
    if _TECH.search(text):
        return "technical"
    if _DELIVERY.search(text):
        return "delivery"
    if any(rx.search(unit.norm) for rx in profile.advantage):
        return "advantage"
    return "functional"


def _classify(unit, profile):
    row = _best(profile, unit.norm)
    if row:
        return row["class"], row["label"]
    if profile.baseline and any(b in unit.norm for b in lexicon.BASELINE):
        return "direct", ""
    return "unsupported", ""


def _requirements(units, profile, year, semantic=None, changes=None):
    reqs, claimed = [], set()
    # named platforms: one technical requirement per distinct tool (a stack list is not one requirement)
    found = {}
    for term, optional in extract.platform_hits(units):
        row = profile.capability(norm(term))
        found.setdefault(row["id"] if row else term, Req("technical", term, row["class"] if row else "unsupported",
                                                         Fraction(1, 2) if optional else Fraction(1), row["label"] if row else "", anchor=bool(row)))
    body = [u for u in units if u.section in ("required", "preferred", "duty")]
    for cap in profile.capabilities:
        if cap["bucket"] == "platform" and cap["id"] not in found and any(rx.search(u.norm) for u in body for rx in cap["rx"]):
            found[cap["id"]] = Req("technical", cap["label"], cap["class"], Fraction(1), cap["label"], anchor=True)
    for name, optional in extract.candidates(units, _PLATFORM_KEYS):
        row = profile.capability(norm(name))
        found.setdefault(row["id"] if row else norm(name).strip(),
                         Req("technical", name, row["class"] if row else "unsupported", Fraction(1, 2) if optional else Fraction(1)))
    platform = list(found.values())
    for r in platform[6:] if len(platform) > 12 else []:           # 15 platforms listed, ~5 are core
        r.weight = min(r.weight, Fraction(1, 2))
    reqs += platform
    for u in units:                                               # everything else: one requirement, one dimension
        if u.section not in ("required", "preferred", "duty", "summary") or u.section == "skip":
            continue
        stated = extract.gated(u)
        if u.section == "duty":
            stated = bool(_best(profile, u.norm) or _ROLE.search(u.text) or _TECH.search(u.text) or _DELIVERY.search(u.text))
        if not stated:
            continue
        if sum(1 for _, rx in extract._PLATFORM_RX if rx.search(u.norm)):
            continue                                              # named tools were taken above, once each
        if extract.scope_hits([u]) and not _best(profile, u.norm) and u.section != "duty":
            continue                                              # team size / budget / reach: scored from scope below
        cls, strength = _classify(u, profile)
        req = Req(_dimension(u, profile), _short(u.text), cls, _weight(u, year), strength, anchor=bool(strength))
        reqs.append(req)
        if semantic is not None and cls == "unsupported":
            changes.append((req, u.text))
    if changes:
        for req, (row, sim, cls) in zip((r for r, _ in changes), semantic.best_many([t for _, t in changes])):
            if row is not None and cls != "unsupported":
                req.cls, req.strength = cls, row["label"]
                req.sim = sim
        changes[:] = [(r, t) for r, t in changes if r.cls != "unsupported"]
    yrs = extract.max_years(units)
    if yrs and profile.years:
        n, unit = yrs
        cls = "direct" if profile.years >= n else "adjacent" if profile.years >= 0.75 * n else "method"
        reqs.append(Req("role", f"{n}+ years", cls, _weight(unit, year), f"{profile.years}+ years"))
    for name, hit in extract.scope_hits([u for u in units if u.section != "preferred"]).items():
        mine = profile.scope.get({"team_size": "team_size", "budget": "budget_usd"}.get(name, name))
        if isinstance(hit, bool) or mine is None or isinstance(mine, str):
            cls = mine if mine in VALUE else "unsupported"
        else:
            cls = "direct" if mine >= hit else "adjacent" if mine >= 0.5 * hit else "method"
        dim = "role" if name in ("executive", "people_leadership") else "delivery"
        reqs.append(Req(dim, name.replace("_", " "), cls, Fraction(1), name.replace("_", " ")))
    return reqs


def _title(title, profile):
    row = profile.function(norm(title)) or profile.capability(norm(title))
    return (row["class"], row["label"]) if row else ("unsupported", "")


def _family(title, units, profile):
    """True when the posting's actual profession is a hard-family mismatch (not one stray requirement)."""
    terms = list(lexicon.HARD_FAMILY)
    extra = profile.hard_family
    hit = lambda text: any(t in text.lower() for t in terms) or any(rx.search(norm(text)) for rx in extra)
    if hit(title or ""):
        return True
    body = [u for u in units if u.section in ("required", "duty")]
    hits = sum(1 for u in body if hit(u.text))
    return hits >= 3 and hits / max(len(body), 1) >= FAMILY_SHARE


JUNIOR = re.compile(r"\b(?:analyst|associate|assistant|intern|coordinator|clerk|trainee|apprentice|junior|graduate|entry[- ]level|early careers?)\b", re.I)
SENIOR = re.compile(r"\b(?:director|vice president|vp|head|principal|manager)\b", re.I)
JUNIOR_CAP = 55          # below the Review band: an analyst-level title is not this profile's level (D89)
UNANCHORED_CAP = 50      # no title or requirement matched a profile function or capability: generic requirements alone are not a Fit (D89)


@dataclass(frozen=True)
class Calc:
    """Exact arithmetic: nothing is rounded until `final`."""
    applicable_max: Fraction
    contributions: dict            # dimension -> exact normalized contribution (0-100 scale)
    uncapped: Fraction             # before the specialization bonus and the cap
    capped: bool
    final: int
    attainment: dict = field(default_factory=dict)   # dimension -> share of its own budget earned (0-100), applicable only


def calculate(reqs, title_cls, bonus=False, capped=False):
    """The V3 formula over classified requirements: Req(dim, label, cls, weight) and the title's evidence class.
    Title is a fixed 7.25 sub-budget of Role/Seniority; the 21.75 JD sub-budget is applicable only when a role requirement exists."""
    title = TITLE_SHARE * EVIDENCE[title_cls]
    applicable, earned = TITLE_SHARE, {d: Fraction(0) for d in BUDGET}
    earned["role"] = title
    for dim, budget in BUDGET.items():
        rows = [r for r in reqs if r.dim == dim]
        if dim == "role":
            budget -= TITLE_SHARE
        if rows:
            applicable += budget
            total = sum(r.weight for r in rows)
            earned[dim] += budget * sum(r.weight * EVIDENCE[r.cls] for r in rows) / total
    scale = Fraction(100) / applicable
    contributions = {d: v * scale for d, v in earned.items() if v or d == "role" or any(r.dim == d for r in reqs)}
    uncapped = sum(earned.values()) * scale
    total = min(Fraction(100), uncapped + (3 if bonus else 0))
    attain = {d: earned[d] / (BUDGET[d] if d != "role" else BUDGET[d]) * 100 for d in BUDGET
              if any(r.dim == d for r in reqs) or d == "role"}
    return Calc(applicable, contributions, uncapped, capped, _half_up(min(total, Fraction(40)) if capped else total), attain)


def _arithmetic(reqs, title_cls, bonus, capped):
    calc = calculate(reqs, title_cls, bonus, capped)
    return calc.final, {d: _half_up(v) for d, v in calc.attainment.items()}


def _line(score, decision, reqs, gate=None):
    strengths = list(dict.fromkeys(r.strength or r.label for r in reqs if r.cls == "direct" and r.strength))[:3]
    gaps = list(dict.fromkeys(r.label for r in sorted(reqs, key=lambda r: -r.weight) if r.cls == "unsupported"))[:3]
    head = f"[{score}%] {decision}" + (f" | Excluded: {gate['reason']}" if gate else "")
    return f"{head} | Strengths: {', '.join(strengths) or 'none evidenced'} | Gaps: {', '.join(gaps) or 'none'}"


def evaluate(title, company, text, profile, today, semantic=None):
    """Professional Fit for one posting, plus the separate exclusion gate. `today` is a date (inflation uses its year)."""
    text = text or ""
    gate, soft = exclusions.check(title, company, text, profile)
    units = extract.parse(text)
    reqs = _requirements(units, profile, today.year) if len(text) >= MIN_TEXT else []
    if not reqs:
        why = "Description too short to score." if len(text) < MIN_TEXT else "No requirement was recognised in the description."
        line = "[--] Unscorable" + (f" | Excluded: {gate['reason']}" if gate else "")
        return Result(None, "No-Go" if gate else "Unscorable", line, why, exclusion=gate["id"] if gate else None, soft=soft)
    title_cls, title_strength = _title(title, profile)
    bonus = title_cls == "direct" and any(rx.search(norm(title)) for rx in profile.specialization)
    capped = _family(title, units, profile)
    score, buckets = _arithmetic(reqs, title_cls, bonus, capped)
    anchored = title_cls != "unsupported" or any(r.anchor for r in reqs)
    junior = bool(JUNIOR.search(title or "")) and not SENIOR.search(title or "")
    if score > UNANCHORED_CAP and not anchored:
        score, capped_note = UNANCHORED_CAP, f"Capped at {UNANCHORED_CAP}: nothing in the title or requirements matches your functions or capabilities."
    elif score > JUNIOR_CAP and junior:
        score, capped_note = JUNIOR_CAP, f"Capped at {JUNIOR_CAP}: an analyst-level title is below this profile's level."
    else:
        capped_note = None
    decision = "No-Go" if gate or score < GO_THRESHOLD else "Go"
    weakest = min(buckets, key=buckets.get)
    if gate:
        why = f"Gate: {gate['reason']} ({gate['where']}); professional Fit is {score}%."
    elif capped_note:
        why = capped_note
    elif capped and score <= 40:
        why = "Capped at 40: the role's profession is a hard-family mismatch."
    elif decision == "No-Go":
        why = f"Below {GO_THRESHOLD}%: weakest area is {NAMES[weakest]} ({buckets[weakest]}%)."
    else:
        why = f"Meets the {GO_THRESHOLD}% bar."
    flat = ([Req("role", _short(title), title_cls, Fraction(1), title_strength)] if title_strength else []) + reqs
    confident = len(reqs) >= 8 and len(text) >= 1200 and len([d for d in buckets if d != "role"]) >= 2
    result = Result(score, decision, _line(score, decision, flat, gate), why, exclusion=gate["id"] if gate else None,
                    confidence="high" if confident else "low", capped=capped,
                    buckets={NAMES[d]: v for d, v in buckets.items()},
                    items=[[r.dim, r.label, r.cls, float(r.weight)] for r in flat][:80], soft=soft)
    if semantic is not None:                       # shadow: the same posting with semantic matches applied; never used
        changes = []
        shadow_reqs = _requirements(units, profile, today.year, semantic, changes)
        shadow, _ = _arithmetic(shadow_reqs, title_cls, bonus, capped)
        result.shadow_score, result.shadow_changes = shadow, len(changes)
        result.shadow_sims = sorted(round(r.sim, 2) for r, _ in changes)
        result.shadow_flip = (not gate) and ((shadow >= GO_THRESHOLD) != (score >= GO_THRESHOLD))
    return result
