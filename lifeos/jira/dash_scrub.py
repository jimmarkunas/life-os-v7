"""JFM-221 (Jim, Oct 29 2026): nothing private reaches the Jim & Matt dashboard. Jira text passes through `scrub` before it becomes Notion text: e-mail addresses, phone
numbers, money amounts, long digit runs (account, card, member or reference numbers) and SSN-shaped numbers become "[private]", and so does every term in
JM_DASHBOARD_REDACT_TERMS when that setting is given (comma-separated: names or words never wanted on a shared page; to be loaded from Bitwarden). The text itself never reaches a log. Pure: no I/O."""
import re

PRIVATE = "[private]"
PATTERNS = (
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),                                                   # e-mail address
    re.compile(r"(?:[$£€]\s?\d[\d,]*(?:\.\d+)?\s?(?:[kKmM](?![a-zA-Z]))?(?:\s?/\s?(?:hour|hr|month|mo|year|yr|week|wk)\b)?)"
               r"|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|GBP|EUR|dollars?|pounds?)\b", re.I),               # money: $220K, £5,000, $70/hr, 500 dollars
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                                                          # SSN shape
    re.compile(r"(?:\+?\d[\s().-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"),                        # phone number
    re.compile(r"\b(?:\d[ -]?){6,}\d\b"),                                                          # 7+ digit runs: account, card or reference numbers
)


def terms(environ):
    raw = (environ.get("JM_DASHBOARD_REDACT_TERMS") or "")
    return tuple(sorted({t.strip() for t in raw.split(",") if len(t.strip()) >= 2}, key=len, reverse=True))


def scrub(text, private_terms=()):
    """-> (clean text, number of spans removed)."""
    out, hits = str(text or ""), 0
    for pattern in PATTERNS:
        out, n = pattern.subn(PRIVATE, out)
        hits += n
    for term in private_terms:
        out, n = re.subn(rf"(?<!\w){re.escape(term)}(?!\w)", PRIVATE, out, flags=re.I)
        hits += n
    return out, hits
