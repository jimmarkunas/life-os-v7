"""Job description -> units (a bullet or sentence tagged with its section) and the raw facts the scorer classifies."""
from dataclasses import dataclass
import re

from lifeos.jobs.fit import lexicon
from lifeos.jobs.fit.profile import norm, term_regex

_PLATFORM_RX = sorted(((t, term_regex(t)) for t in lexicon.PLATFORMS), key=lambda p: -len(p[0]))
_STRICT = re.compile(r"required|must|minimum|mandatory|basic qualification", re.I)
_OPTIONAL = re.compile(lexicon.OPTIONAL, re.I)
_SHAPE = re.compile(lexicon.SHAPE, re.I)
_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:\+\s*)?(?:years?|yrs?)\b", re.I)
_TRIGGER = re.compile(r"(?:experience|proficien\w*|knowledge|familiar\w*|expertise|hands-on|background)\s+"
                      r"(?:with|in|of|using|on)\s+([^.;:()\n]{3,140})", re.I)
_NOT_TECH = {"english", "spanish", "french", "german", "bachelor", "master", "masters", "mba", "phd", "us", "usa", "uk",
             "united", "states", "fluent", "strong", "excellent", "proven", "good", "deep", "solid", "senior", "the",
             "a", "an", "our", "your", "ability", "understanding", "working", "hands", "agile", "rest", "json", "xml", "http", "html", "css", "kpi", "kpis", "okr", "okrs", "roi", "b2b", "b2c", "saas", "sla", "slas"}
_SPLIT = re.compile(r",|/|\band\b|\bor\b|&|\bsuch as\b|\be\.g\.|\bincluding\b|\bi\.e\.")


@dataclass(frozen=True)
class Unit:
    text: str
    section: str          # summary | required | preferred | duty | skip
    strict: bool = False  # the heading itself makes it mandatory (Requirements / Minimum / Must have)

    @property
    def norm(self):
        return norm(self.text)

    @property
    def optional(self):
        return self.section == "preferred" or bool(_OPTIONAL.search(self.text))


def _heading(line):
    bare = line.strip(" :*-•#\t").lower()
    if not bare or line.startswith("•") or len(bare) > 70 or len(bare.split()) > 9:
        return None
    for kind, pattern in lexicon.HEADINGS:
        if re.search(pattern, bare):
            return kind
    return None


# D114: equal-opportunity and application-legal sentences sit under a "requirements" heading in some postings (Revolut) and match the requirement shape
# ("background", "required"). They are not requirements and must never count as gaps.
_BOILER = re.compile(r"encourage applications|diverse backgrounds?|by submitting (?:this|your) application|equal opportunit|we are committed to|"
                     r"privacy (?:notice|policy)|reasonable adjustments?|regardless of (?:race|gender|age)", re.I)


def parse(text):
    """Units in document order. A line that is a known heading switches section; long paragraphs split to sentences."""
    units, section, strict = [], "summary", False
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        kind = _heading(line)
        if kind:
            section = kind
            strict = kind == "required" and bool(_STRICT.search(line))
            continue
        if line.endswith(":") and len(line) < 90:
            continue
        pieces = [line.lstrip("• ").strip()] if line.startswith("•") or len(line) < 220 else \
            [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line)]
        units += [Unit(p, section, strict) for p in pieces if len(p) > 3 and not _BOILER.search(p)]
    if not any(u.section in ("required", "preferred", "duty") for u in units):
        units = [Unit(u.text, "required", False) for u in units]       # no recognisable headings: every sentence is judged on its shape
    return units


def gated(unit):
    """Does a unit state a requirement? Explicit required/preferred bullets with requirement shape, or an
    anywhere-sentence that says required/must/minimum."""
    if unit.section in ("required", "preferred"):
        return bool(_SHAPE.search(unit.text))
    return unit.section in ("summary", "duty") and bool(re.search(r"\b(required|must have|minimum of|requires)\b", unit.text, re.I))


def requirement_units(units):
    return [u for u in units if gated(u)][:60]


def duty_units(units):
    return [u for u in units if u.section == "duty"][:40]


def platform_hits(units):
    """Distinct technology terms in requirement-bearing sections, in order of appearance, required first.
    Returns [(term, optional)]; a term is required when any required-section, non-optional unit names it."""
    seen = {}
    for u in units:
        if u.section not in ("required", "preferred", "duty"):
            continue
        required = u.section == "required" and not u.optional
        text = u.norm
        for term, rx in _PLATFORM_RX:
            if rx.search(text):
                seen[term] = seen.get(term, True) and not required
    ordered = sorted(seen.items(), key=lambda kv: kv[1])
    return ordered


def candidates(units, known):
    """Capitalised names after 'experience with ...' that the vocabulary does not know (unsupported until the
    profile says otherwise)."""
    out = {}
    for u in units:
        if u.section not in ("required", "preferred"):
            continue
        for m in _TRIGGER.finditer(u.text):
            for piece in _SPLIT.split(m.group(1)):
                piece = piece.strip(" .-")
                words = piece.split()
                if not 1 <= len(words) <= 3 or not all(re.match(r"^[A-Z0-9][\w.+#-]*$", w) for w in words):
                    continue
                if any(w.lower() in _NOT_TECH for w in words) or norm(piece).strip() in known:
                    continue
                out.setdefault(piece, u.optional)
    return list(out.items())


def max_years(units):
    """The largest 'N years' demanded by a requirement unit -> (years, unit) or None."""
    best = None
    for u in units:
        if u.section not in ("required", "summary") or u.optional:
            continue
        for m in _YEARS.finditer(u.text):
            n = int(m.group(1))
            if 1 <= n <= 40 and (best is None or n > best[0]):
                best = (n, u)
    return best


_SCOPE = (
    ("people_leadership", r"\b(?:manage|lead|leading|oversee|direct|mentor|supervis)\w*\s+(?:a\s+|the\s+)?(?:team|teams|engineers|people|staff|organization)"),
    ("team_size", r"team of (\d+)|(\d+)\+?\s+(?:direct reports|engineers|people|team members)"),
    ("budget", r"\$\s?(\d+(?:\.\d+)?)\s?(m|mm|million|b|billion|k)\b"),
    ("global", r"\b(?:global|international|multi-?country|multi-?market|worldwide|emea|apac|cross-border|multi-?region)\b"),
    ("enterprise", r"\b(?:enterprise|fortune 500|large[- ]scale|complex (?:programs?|systems?|organi[sz]ations?))\b"),
    ("executive", r"\b(?:c-?level|executive|vp|vice president|senior leadership|board|sponsor)\b"),
    ("multi_team", r"\b(?:cross-?functional|multi-?team|multiple teams|multiple workstreams|portfolio)\b"),
)


def scope_hits(units):
    """{concept: number-or-True} found in requirement/duty/summary text."""
    found = {}
    for u in units:
        if u.section == "skip":
            continue
        for name, pattern in _SCOPE:
            m = re.search(pattern, u.text, re.I)
            if not m:
                continue
            if name == "team_size":
                found[name] = max(found.get(name, 0), int(next(g for g in m.groups() if g)))
            elif name == "budget":
                unit = m.group(2).lower()
                factor = {"k": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}[unit]
                found[name] = max(found.get(name, 0), float(m.group(1)) * factor)
            else:
                found[name] = True
    return found
