"""Name and role comparison shared by every OS (Jobs: hiring-pipeline handoff, sponsor register; Interview: identity).

Deterministic, no fuzzy scoring beyond the fixed token rules below. A change here changes every consumer: update
tests/platform/test_names.py in the same commit."""
import re

_BRITISH = re.compile(r"\b(?:(organis|optimis|prioritis|specialis|standardis|utilis)(?=[a-z]*\b)|(programme|centre|analyse|licence|catalogue|modelling|labour)(?=(?:s|d)?\b))")
_AMERICAN = {"programme": "program", "organis": "organiz", "optimis": "optimiz", "prioritis": "prioritiz", "specialis": "specializ",
             "standardis": "standardiz", "utilis": "utiliz", "centre": "center", "analyse": "analyze", "licence": "license",
             "catalogue": "catalog", "modelling": "modeling", "labour": "labor"}

LEGAL = {"inc", "llc", "ltd", "limited", "corp", "corporation", "co", "plc", "gmbh", "company", "the", "incorporated", "lp", "llp", "uk", "u.k"}
GENERIC = {"group", "holdings", "holding", "international", "global", "europe", "services", "technologies", "technology", "tech", "solutions",
           "systems", "software", "labs", "digital", "bank", "worldwide", "partners", "consulting", "emea", "services"}
TITLE_SPLIT = re.compile(r"\s+[—–-]\s+|\s*[—–]\s*|:\s+")


def norm(text: str | None) -> str:
    """Lower-case, British spellings to American, punctuation to spaces except characters technology names use,
    single spaced."""
    lowered = _BRITISH.sub(lambda m: _AMERICAN[m.group(1) or m.group(2)], (text or "").lower())
    return " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#./&-]+", " ", lowered)).strip() + " "


def tokens(name: str | None) -> list[str]:
    out = (t.strip(".") for t in norm(name).split())
    return [t for t in out if t and t not in LEGAL]


def core(name: str | None) -> list[str]:
    """The distinctive tokens: legal and generic trailing words dropped ("Monzo Bank Limited" -> ["monzo"])."""
    out = tokens(name)
    while len(out) > 1 and out[-1] in GENERIC:
        out.pop()
    return out


def same_employer(a: str | None, b: str | None) -> bool:
    x, y = core(a), core(b)
    return bool(x) and x == y and not (x and x[0] in GENERIC)


def _plain_tokens(text: str | None) -> list[str]:
    return [t for t in norm(text).split() if t not in LEGAL]


def same_company(a: str | None, b: str | None) -> bool:
    x, y = _plain_tokens(a), _plain_tokens(b)
    if not x or not y:
        return False
    if x == y:
        return True
    short, long = (x, y) if len(x) < len(y) else (y, x)
    return long[:len(short)] == short and long[len(short)] not in ("&", "and", "+")


def same_role(a: str | None, b: str | None) -> bool:
    x, y = set(_plain_tokens(a)), set(_plain_tokens(b))
    if not x or not y:
        return False
    return len(x & y) / len(x | y) >= 0.8


def split_title(title: str | None) -> tuple[str | None, str]:
    """('Company', 'Role') from a canonical "<Company> — <Role>" title, else (title, '')."""
    parts = TITLE_SPLIT.split((title or "").strip(), maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else (title, "")
