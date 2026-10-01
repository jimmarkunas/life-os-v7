"""Lane policy: one data-driven qualification for every acquisition lane (docs/LANES.md).

Professional Fit is shared and lives in lifeos.jobs.fit (floor 72 for every lane). A lane only adds opportunity policy:
market, work mode, explicit pay, posting age, route and geography evidence. Unresolved evidence is REVIEW, never guessed;
definitive negative evidence (and a closed vacancy) is EXCLUDE.
"""
from dataclasses import dataclass, field
from datetime import date
import re

FIT_FLOOR = 72
POSITIVE, NEGATIVE, UNRESOLVED = "POSITIVE", "NEGATIVE", "UNRESOLVED"
ADMIT, REVIEW, EXCLUDE, DISABLED = "ADMIT", "REVIEW", "EXCLUDE", "DISABLED"
PRECEDENCE = ("Scale-Up", "Skilled Worker", "US Remote")      # the visible lane when a job is eligible for several


@dataclass(frozen=True)
class LanePolicy:
    name: str
    market: str                          # "US" | "UK"
    currency: str                        # the symbol explicit pay is judged in
    work_mode: str = "any"               # "remote_only" | "any"
    pay_floor: int | None = None         # annual, only when pay is explicit
    max_age_days: int | None = None      # new-admission freshness; None = no gate
    unknown_date_blocks: bool = True     # a missing posting date goes to REVIEW (False: it does not suppress)
    route: str | None = None             # required route evidence ("Scale-up", "Skilled Worker")
    geography_required: bool = False
    enabled: bool = True
    bucket: str = "Curated"              # Curated | Target (sourcing priority only; never a different Fit floor)


POLICIES = {
    "US Remote": LanePolicy("US Remote", "US", "$", work_mode="remote_only", pay_floor=80_000, max_age_days=14),
    # Scale-Up: the liberal lane. Any work mode, no pay floor, route + geography evidence required, 30-day age gate
    # (Jim, 2026-10-01; the earlier canon had no age gate). A missing posting date does not suppress it.
    "Scale-Up": LanePolicy("Scale-Up", "UK", "£", max_age_days=30, unknown_date_blocks=False, route="Scale-up",
                           geography_required=True, bucket="Target"),
    "Skilled Worker": LanePolicy("Skilled Worker", "UK", "£", pay_floor=65_000, max_age_days=14, route="Skilled Worker",
                                 enabled=False, bucket="Target"),
}


@dataclass
class Facts:
    fit: int | None                      # professional Fit, None = unscorable
    market: str | None = None            # "US" | "UK" | None (unknown)
    work_mode: str = "unknown"           # remote | hybrid | onsite | unknown
    pay_min: int | None = None           # annual minimum, in `pay_currency`, only when explicit
    pay_currency: str | None = None
    posted: date | None = None
    closed: bool = False                 # definitive removal / closure evidence
    route: dict = field(default_factory=dict)   # {"Scale-up": POSITIVE, ...}
    geography: str = UNRESOLVED


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str | None = None


def qualify(policy, facts, today):
    if not policy.enabled:
        return Decision(DISABLED, "lane disabled")
    if facts.market and facts.market != policy.market:
        return Decision(EXCLUDE, f"market {facts.market} is not {policy.market}")
    if facts.closed:
        return Decision(EXCLUDE, "vacancy closed")
    if facts.fit is not None and facts.fit < FIT_FLOOR:
        return Decision(EXCLUDE, f"Fit {facts.fit} below {FIT_FLOOR}")
    if policy.work_mode == "remote_only":
        if facts.work_mode == "unknown":
            return Decision(REVIEW, "work mode unresolved")
        if facts.work_mode != "remote":
            return Decision(EXCLUDE, "not remote")
    if (policy.pay_floor and facts.pay_min is not None and facts.pay_currency == policy.currency
            and facts.pay_min < policy.pay_floor):
        return Decision(EXCLUDE, f"explicit pay below {policy.currency}{policy.pay_floor:,}")
    if policy.max_age_days is not None:
        if facts.posted is None:
            if policy.unknown_date_blocks:
                return Decision(REVIEW, "posting date unresolved")
        elif (today - facts.posted).days < 0:
            return Decision(REVIEW, "posting date in the future")
        elif (today - facts.posted).days > policy.max_age_days:
            return Decision(EXCLUDE, f"older than {policy.max_age_days} days")
    if policy.route:
        state = facts.route.get(policy.route, UNRESOLVED)
        if state == NEGATIVE:
            return Decision(EXCLUDE, f"{policy.route} route negative")
        if state != POSITIVE:
            return Decision(REVIEW, f"{policy.route} route unresolved")
    if policy.geography_required:
        if facts.geography == NEGATIVE:
            return Decision(EXCLUDE, "geography does not qualify")
        if facts.geography != POSITIVE:
            return Decision(REVIEW, "geography unresolved")
    if facts.fit is None:
        return Decision(REVIEW, "Fit unscorable")
    return Decision(ADMIT)


def qualify_all(facts, today, policies=POLICIES):
    """{lane: Decision} for every enabled lane, plus the one visible lane (None when no lane admits it)."""
    results = {name: qualify(p, facts, today) for name, p in policies.items() if p.enabled}
    visible = next((n for n in PRECEDENCE if n in results and results[n].status == ADMIT), None)
    return results, visible


_PAY = re.compile(r"([$£€])\s?(\d[\d,]*(?:\.\d+)?)\s?(k|m)?\b(?:\s*(?:/|per|a)\s*(yr|year|annum|hr|hour))?", re.I)


def parse_pay(text):
    """(annual minimum, currency symbol) from posted pay text, or (None, None) when it is not clearly explicit.
    Hourly rates are annualised at 2,080 hours; a bare number with no currency symbol is never read as pay."""
    best = None
    for symbol, number, scale, unit in _PAY.findall(text or ""):
        value = float(number.replace(",", ""))
        value *= {"k": 1_000, "m": 1_000_000}.get((scale or "").lower(), 1)
        if (unit or "").lower() in ("hr", "hour"):
            value *= 2080
        elif not scale and not unit and value < 1000:
            continue                                        # "$5" with no period is not an annual figure
        best = (int(value), symbol) if best is None or value < best[0] else best
    return best if best else (None, None)


def detect_work_mode(location, title="", text=""):
    """remote | hybrid | onsite | unknown. The location and title decide; the description only confirms explicit statements."""
    head = f"{location or ''} {title or ''}".lower()
    body = (text or "")[:1500].lower()
    if re.search(r"\bhybrid\b", head) or re.search(r"\bhybrid (?:role|work|schedule|position)\b|\bdays? (?:a|per) week in (?:the )?office", body):
        return "hybrid"
    if re.search(r"\bremote\b|\bwork from home\b|\banywhere\b", head):
        return "remote"
    if re.search(r"\b(?:fully|100%|completely) remote\b|\bremote[- ]first\b|\bwork from anywhere\b|\bthis is a remote (?:role|position)\b", body):
        return "remote"
    if re.search(r"\bon-?site\b|\bin[- ]office\b|\bonsite\b", head + " " + body):
        return "onsite"
    return "unknown"
