"""Lane policy: one data-driven qualification for every acquisition lane (docs/LANES.md).

Professional Fit is shared and lives in lifeos.jobs.fit (floor 68 for every lane; below it a job goes to Review, D81). A lane only adds opportunity policy:
market, work mode, explicit pay, posting age, route and geography evidence. Unresolved evidence is REVIEW, never guessed;
definitive negative evidence (and a closed vacancy) is EXCLUDE.
"""
from dataclasses import dataclass, field
from datetime import date
import re

FIT_FLOOR = 68
REVIEW_FLOOR = 60                       # D84: Fit 60-67 is Review (near miss); below 60 is excluded
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
    london_only: bool = False            # D96: the job must be in London; a stated place that is not London is EXCLUDE (an empty location stays Review)
    enabled: bool = True
    market_required: bool = False        # an unknown market is REVIEW (the UK lanes: never assume a job is in the UK)
    bucket: str = "Curated"              # Curated | Target (sourcing priority only; never a different Fit floor)


POLICIES = {
    "US Remote": LanePolicy("US Remote", "US", "$", work_mode="remote_only", pay_floor=75_000, max_age_days=14),
    # Scale-Up: the liberal lane. Any work mode, no pay floor, route + geography evidence required, 30-day age gate
    # (Jim, 2026-10-01; the earlier canon had no age gate). A missing posting date does not suppress it.
    "Scale-Up": LanePolicy("Scale-Up", "UK", "£", pay_floor=40_000, max_age_days=30, unknown_date_blocks=False, route="Scale-up",
                           geography_required=True, london_only=True, bucket="Target"),
    # Skilled Worker (Phase 2, enabled by Jim 2026-10-01): sponsor-register evidence for the actual employer is the route; London positive, a named
    # non-target place negative, UK-remote / unresolved geography goes to Review; explicit pay under GBP 65,000 excludes; 14 days; Fit 68 like every lane.
    "Skilled Worker": LanePolicy("Skilled Worker", "UK", "£", pay_floor=65_000, max_age_days=14, route="Skilled Worker",
                                 geography_required=True, market_required=True, bucket="Target"),
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
    located: bool = False                # the job states a place at all
    first_party: bool = False            # D111: listed on the employer's own board, so it is open: its posting age is not judged


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str | None = None


def qualify(policy, facts, today):
    """D100: Review is for ONE open question on a job that otherwise clearly fits. A near-miss Fit (60-67) with any other evidence also unresolved
    (work mode, posting date, route, geography) is not Review, it is EXCLUDE: two doubts on a weak fit is a miss, not a question for Jim."""
    decision = _qualify(policy, facts, today)
    if decision.status == REVIEW and facts.fit is not None and facts.fit < FIT_FLOOR and not (decision.reason or "").startswith("Fit "):
        return Decision(EXCLUDE, f"Fit {facts.fit} below {FIT_FLOOR} and {decision.reason}")
    return decision


def _qualify(policy, facts, today):
    if not policy.enabled:
        return Decision(DISABLED, "lane disabled")
    if facts.market and facts.market != policy.market:
        return Decision(EXCLUDE, f"market {facts.market} is not {policy.market}")
    if policy.market_required and not facts.market:
        return Decision(REVIEW, "market unresolved")
    if facts.closed:
        return Decision(EXCLUDE, "vacancy closed")
    if facts.fit is not None and facts.fit < REVIEW_FLOOR:
        return Decision(EXCLUDE, f"Fit {facts.fit} below {REVIEW_FLOOR}")
    if policy.work_mode == "remote_only":
        if facts.work_mode == "unknown":
            return Decision(REVIEW, "work mode unresolved")
        if facts.work_mode != "remote":
            return Decision(EXCLUDE, "not remote")
    if (policy.pay_floor and facts.pay_min is not None and facts.pay_currency == policy.currency
            and facts.pay_min < policy.pay_floor):
        return Decision(EXCLUDE, f"explicit pay below {policy.currency}{policy.pay_floor:,}")
    if policy.max_age_days is not None and not facts.first_party:
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
        if policy.london_only and facts.geography != POSITIVE and facts.located:
            return Decision(EXCLUDE, "not in London")
        if facts.geography == NEGATIVE:
            return Decision(EXCLUDE, "geography does not qualify")
        if facts.geography != POSITIVE:
            return Decision(REVIEW, "geography unresolved")
    if facts.fit is None:
        return Decision(REVIEW, "Fit unscorable")
    if facts.fit < FIT_FLOOR:
        return Decision(REVIEW, f"Fit {facts.fit} below {FIT_FLOOR}")
    return Decision(ADMIT)


def qualify_all(facts, today, policies=POLICIES, own=None):
    """{lane: Decision} for every enabled lane, plus the one visible lane (None when no lane admits it)."""
    results = {name: qualify(p, facts, today) for name, p in policies.items() if p.enabled}
    if own is not None:                                                # D102: a lane that is not the job's own admits it only on a POSITIVE market match
        for name, p in policies.items():
            if name != own and name in results and results[name].status == ADMIT and facts.market != p.market:
                results[name] = Decision(EXCLUDE, f"market not shown to be {p.market}")
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


def detect_work_mode(location, title="", text="", window=1500):
    """remote | hybrid | onsite | unknown. The location and title decide; the description only confirms explicit statements (in its first `window` characters)."""
    head = f"{location or ''} {title or ''}".lower()
    body = (text or "")[:window].lower()
    if re.search(r"\bhybrid\b", head) or re.search(r"\bhybrid (?:role|work|schedule|position)\b|\bdays? (?:a|per) week in (?:the )?office", body):
        return "hybrid"
    if re.search(r"\bremote\b|\bwork from home\b|\banywhere\b", head):
        return "remote"
    if re.search(r"\b(?:fully|100%|completely) remote\b|\bremote[- ]first\b|\bwork from anywhere\b|\bthis is a remote (?:role|position)\b", body):
        return "remote"
    if re.search(r"\bon-?site\b|\bin[- ]office\b|\bonsite\b", head + " " + body):
        return "onsite"
    return "unknown"


LANE_ALIAS = {"Newsletter": "US Remote"}        # a newsletter is a source family; its jobs are judged by the US Remote policy
ADMISSION_LABEL = {ADMIT: "Admitted", REVIEW: "Passed / Review", EXCLUDE: "Excluded"}   # the Ledger's Admission Status options
POLICY_VERSION = "l14"                            # bump when a policy changes so stored decisions are re-evaluated


def lane_for(row_lane):
    return LANE_ALIAS.get(row_lane or "Newsletter", row_lane or "US Remote")


_UK_REMOTE = re.compile(r"remote\W{0,6}(?:uk|u\.k\.|united kingdom|england|great britain|gb)\b|\b(?:uk|united kingdom|england|great britain)\W{0,6}remote")


def geography_status(location):
    """Scale-Up geography (V2 rules, target London): London is POSITIVE, a named non-target place is NEGATIVE, anything else UNRESOLVED."""
    text = (location or "").casefold()
    if not text:
        return UNRESOLVED
    if "london" in text or _UK_REMOTE.search(text):
        return POSITIVE                                  # D98: a UK-wide remote role (Remote: UK / United Kingdom / England) includes London
    if any(place in text for place in ("ontario", "canada", "manchester", "paris", "new york")):
        return NEGATIVE
    return UNRESOLVED


_UK = re.compile(r"\bUK\b|\bU\.K\.|\b(united kingdom|england|scotland|wales|northern ireland|london|manchester|birmingham|leeds|bristol|edinburgh|glasgow|cardiff|belfast|cambridge|oxford)\b", re.I)
_CANADA = re.compile(r"\b(canada|ontario|toronto|vancouver|montreal|alberta|british columbia|quebec)\b|,\s*(?:ON|BC|AB|QC)\b", re.I)
_US = re.compile(r"(?i:\bunited states\b|\busa\b)|\bU\.?S\.?A?\b|,\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b")
_OTHER = re.compile(r"\b(ireland|dublin|germany|berlin|munich|france|paris|spain|madrid|barcelona|netherlands|amsterdam|poland|warsaw|"
                    r"india|bangalore|bengaluru|hyderabad|pune|mumbai|australia|sydney|melbourne|singapore|japan|tokyo|brazil|mexico|israel|"
                    r"philippines|ukraine|portugal|lisbon|europe|emea|apac|latam|worldwide|global)\b", re.I)


def market_of(location):
    """'US' | 'UK' | 'OTHER' from the location text alone; None when it is silent, ambiguous or names several markets (never guessed)."""
    text = location or ""
    found = []
    if _CANADA.search(text) or _OTHER.search(text):
        found.append("OTHER")
    if _UK.search(text) and not _CANADA.search(text):                # "London, ON" is Canada
        found.append("UK")
    if _US.search(text):
        found.append("US")
    return found[0] if len(found) == 1 else None


def route_dict(stored):
    """'Scale-up:POSITIVE;Skilled Worker:NEGATIVE' (as stored on the job) -> {'Scale-up': 'POSITIVE', 'Skilled Worker': 'NEGATIVE'}."""
    out = {}
    for part in (stored or "").split(";"):
        name, _, state = part.partition(":")
        if name.strip() and state.strip():
            out[name.strip()] = state.strip()
    return out


def join_routes(stored, **extra):
    """Add route states to the stored string without losing what is there: join_routes('Scale-up:POSITIVE', **{'Skilled Worker': 'POSITIVE'})."""
    merged = {**route_dict(stored), **extra}
    return ";".join(f"{k}:{v}" for k, v in merged.items())


FIT_WINDOW = 6000        # D92: the Fit stage reads the whole top of the description for an explicit work-mode statement, not only the first 1,500 characters
_CITY_STATE = re.compile(r"^\s*[A-Za-z][A-Za-z .'-]*,\s*[A-Z]{2}\b")


def work_mode_for_fit(location, title, text):
    """detect_work_mode, then (Fit stage only, D89): a named US city and state with no remote, hybrid or office cue anywhere is an office job.
    Acquisition filters keep the plain detector, which never guesses."""
    mode = detect_work_mode(location, title, text, window=FIT_WINDOW)
    return "onsite" if mode == "unknown" and _CITY_STATE.match(location or "") else mode


def first_party_source(source):
    """True for a job read off an employer's own board by the web pass (source web:<id>), not off the Open Jobs feed or an aggregator (D111)."""
    return bool(source) and source.startswith("web:") and source != "web:openjobs"


def facts_for(fit, title, location, text, salary_text, posted, first_seen, market=None, route=None, first_party=False):
    """Facts for one stored job. Age uses the employer Posting Date, else First Surfaced (never a crawl time invented as a
    posting date). Pay comes only from the posted pay field."""
    pay_min, currency = parse_pay(salary_text)
    when = posted or (first_seen.date() if hasattr(first_seen, "date") else first_seen)
    return Facts(fit=fit, market=market or market_of(location), work_mode=work_mode_for_fit(location, title, text), pay_min=pay_min,
                 pay_currency=currency, posted=when, route=route_dict(route), geography=geography_status(location), located=bool((location or "").strip()), first_party=first_party)


def decide_all(row_lane, facts, today, exclusion=None):
    """-> (Decision, visible lane, eligible lanes). The job's own lane judges it first; when another enabled lane admits it and its own does not
    (a UK newsletter job at a licensed sponsor), that lane takes it. A lane that already admits it keeps it unless a lane that precedes it also
    admits (Scale-Up wins a dual route). A private exclusion rule excludes in every lane, with the Fit untouched."""
    own = lane_for(row_lane)
    if exclusion:
        return Decision(EXCLUDE, f"excluded: {exclusion}"), own, []
    results, visible = qualify_all(facts, today, own=own)
    eligible = [n for n in PRECEDENCE if n in results and results[n].status == ADMIT]
    if own in results and results[own].status == ADMIT:
        lane = visible if visible and PRECEDENCE.index(visible) < PRECEDENCE.index(own) else own
        return results[lane], lane, eligible
    if visible:
        return results[visible], visible, eligible
    return qualify(POLICIES[own], facts, today), own, eligible


def decide(row_lane, facts, today, exclusion=None):
    """-> (Decision, lane name)."""
    decision, lane, _ = decide_all(row_lane, facts, today, exclusion)
    return decision, lane
