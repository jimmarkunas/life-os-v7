"""Ledger fields derived from what the pipeline already knows about a job: Location / Work Mode, Market, Visa Route, Compensation.
Pure: no I/O. Only populated fields are returned (a blank is better than a guess); never touches human state."""
import re

from lifeos.jobs import lanes
from lifeos.platform.notion_client import rich_text

META_VERSION = 1            # bump to re-sync these fields onto already published pages (fit_sync picks pages with an older version)

_AMOUNT = r"[$£€]\s?\d[\d,]*(?:\.\d+)?\s?(?:[kK]|[mM]\b)?"
_RANGE = re.compile(rf"{_AMOUNT}\s*(?:-|–|—|to)\s*(?:[$£€]\s?)?\d[\d,]*(?:\.\d+)?\s?(?:[kK]|[mM]\b)?(?:\s*(?:/|per|a|an)\s*(?:yr|year|annum|hr|hour)\b|\s*(?:USD|GBP|EUR)\b)?", re.I)
_SINGLE = re.compile(rf"{_AMOUNT}\s*(?:/|per|a)\s*(?:yr|year|annum)\b|{_AMOUNT}\s*annually\b", re.I)


def pay_text(salary_text, full_text):
    """Posted pay as shown on the job: the stored pay field, else an explicit range (or per-year figure) in the description. '' when none."""
    if salary_text and lanes.parse_pay(salary_text)[0]:
        return salary_text.strip()[:80]
    text = full_text or ""
    for pattern in (_RANGE, _SINGLE):
        for match in pattern.finditer(text):
            value = match.group(0).strip()
            minimum = lanes.parse_pay(value)[0]
            if minimum and minimum >= 15_000:
                return re.sub(r"\s+", " ", value)[:80]
    return ""


def market_label(location):
    """The Ledger's Market options are US and London. A UK location that is not London stays blank rather than guessed."""
    market = lanes.market_of(location)
    if market == "US":
        return "US"
    if market == "UK" and "london" in (location or "").casefold():
        return "London"
    return None


def visa_routes(route_evidence, eligible, lane):
    stored = lanes.route_dict(route_evidence)
    names = {n for n in (eligible or "").split(",") if n}
    routes = []
    if stored.get("Scale-up") == lanes.POSITIVE or "Scale-Up" in names or lane == "Scale-Up":
        routes.append("Scale-up")
    if stored.get("Skilled Worker") == lanes.POSITIVE or "Skilled Worker" in names or lane == "Skilled Worker":
        routes.append("Skilled Worker")
    return routes or ["None / Unknown"]


def properties(location, salary_text, full_text, route_evidence, eligible, lane):
    props = {"Visa Route": {"multi_select": [{"name": n} for n in visa_routes(route_evidence, eligible, lane)]}}
    if location and location.strip():
        props["Location / Work Mode"] = {"rich_text": rich_text(location.strip()[:200])}
    market = market_label(location)
    if market:
        props["Market"] = {"select": {"name": market}}
    pay = pay_text(salary_text, full_text)
    if pay:
        props["Compensation"] = {"rich_text": rich_text(pay)}
    return props
