"""Name and role comparison shared by every OS (Jobs: hiring-pipeline handoff, sponsor register; Interview: parent and child identity).

Moved from `lifeos.jobs` when Interview OS became the second consumer, so there is exactly one normalizer. Deterministic, no fuzzy scoring beyond the
fixed token rules below. A change here changes every consumer: update the tests in tests/platform/test_names.py in the same commit."""
import re

_BRITISH = re.compile(r"\b(?:(organis|optimis|prioritis|specialis|standardis|utilis)(?=[a-z]*\b)|(programme|centre|analyse|licence|catalogue|modelling|labour)(?=(?:s|d)?\b))")
_AMERICAN = {"programme": "program", "organis": "organiz", "optimis": "optimiz", "prioritis": "prioritiz", "specialis": "specializ",
             "standardis": "standardiz", "utilis": "utiliz", "centre": "center", "analyse": "analyze", "licence": "license",
             "catalogue": "catalog", "modelling": "modeling", "labour": "labor"}

LEGAL = {"inc", "llc", "ltd", "limited", "corp", "corporation", "co", "plc", "gmbh", "company", "the", "incorporated", "lp", "llp", "uk", "u.k"}
GENERIC = {"group", "holdings", "holding", "international", "global", "europe", "services", "technologies", "technology", "tech", "solutions",
           "systems", "software", "labs", "digital", "bank", "worldwide", "partners", "consulting", "emea", "services"}
TITLE_SPLIT = re.compile(r"\s+[—–-]\s+|\s*[—–]\s*|:\s+")          # "Company — Role": em/en dash, a spaced hyphen, or "Company: Role"


def norm(text):
    """Lower-case, British spellings to American (a UK "Technical Programme Manager" is a Technical Program Manager), punctuation to
    spaces except the characters technology names use, single spaced. Profile terms and posting text both pass through here."""
    lowered = _BRITISH.sub(lambda m: _AMERICAN[m.group(1) or m.group(2)], (text or "").lower())
    return " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#./&-]+", " ", lowered)).strip() + " "


def tokens(name):
    out = (t.strip(".") for t in norm(name).split())
    return [t for t in out if t and t not in LEGAL]


def core(name):
    """The distinctive tokens: legal and generic trailing words dropped ("Monzo Bank Limited" -> ["monzo"])."""
    out = tokens(name)
    while len(out) > 1 and out[-1] in GENERIC:
        out.pop()
    return out


def same_employer(a, b):
    """Equal distinctive tokens. A bare shared first word is not enough ("Smith" is not "Smith & Wesson")."""
    x, y = core(a), core(b)
    return bool(x) and x == y and not (x and x[0] in GENERIC)


def _plain_tokens(text):
    return [t for t in norm(text).split() if t not in LEGAL]


def same_company(a, b):
    x, y = _plain_tokens(a), _plain_tokens(b)
    if not x or not y:
        return False
    if x == y:
        return True
    short, long = (x, y) if len(x) < len(y) else (y, x)
    # "Walmart" ~ "Walmart Global Tech"; never a bare substring, and "Smith" is not "Smith & Wesson"
    return long[:len(short)] == short and long[len(short)] not in ("&", "and", "+")


def same_role(a, b):
    x, y = set(_plain_tokens(a)), set(_plain_tokens(b))
    if not x or not y:
        return False
    return len(x & y) / len(x | y) >= 0.8


def split_title(title):
    """('Company', 'Role') from a canonical "<Company> — <Role>" title, else (title, '') — a malformed title is never guessed into shape."""
    parts = TITLE_SPLIT.split((title or "").strip(), maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else (title, "")
