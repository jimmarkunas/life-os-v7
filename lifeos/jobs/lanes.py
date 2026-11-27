"""Lane policy: one data-driven qualification for every acquisition lane (docs/LANES.md).

Fit is shared across lanes; a lane adds market, work mode, pay, age, route and geography policy. Unresolved
evidence is REVIEW; definitive negative evidence (and a closed vacancy) is EXCLUDE.

"""
from dataclasses import dataclass, field
from datetime import date
import re

from lifeos.jobs.fit import UNIVERSAL_FIT_FLOOR

FIT_FLOOR = UNIVERSAL_FIT_FLOOR  # ADMIT needs this Fit in every lane
NEAR_MISS_FLOOR = 68
REVIEW_FLOOR = 60
POSITIVE, NEGATIVE, UNRESOLVED = "POSITIVE", "NEGATIVE", "UNRESOLVED"
ADMIT, REVIEW, EXCLUDE, DISABLED = "ADMIT", "REVIEW", "EXCLUDE", "DISABLED"
# not an acquisition lane: decide_all admits it without the Fit gate
RECRUITER = "Recruiter"
PRECEDENCE = ("Scale-Up", "Skilled Worker", "US Remote")


@dataclass(frozen=True)
class LanePolicy:
    name: str
    market: str
    currency: str
    work_mode: str = "any"
    pay_floor: int | None = None
    max_age_days: int | None = None
    unknown_date_blocks: bool = True
    first_party_age: bool = False
    route: str | None = None
    geography_required: bool = False
    london_only: bool = False
    enabled: bool = True
    market_required: bool = False
    bucket: str = "Curated"


POLICIES = {
    "US Remote": LanePolicy("US Remote", "US", "$", work_mode="remote_only", pay_floor=75_000, max_age_days=14, first_party_age=True),
    "Scale-Up": LanePolicy("Scale-Up", "UK", "£", pay_floor=40_000, max_age_days=30, unknown_date_blocks=False, first_party_age=True, route="Scale-up",
                           geography_required=True, london_only=True, bucket="Target"),
    "Skilled Worker": LanePolicy("Skilled Worker", "UK", "£", pay_floor=65_000, max_age_days=14, route="Skilled Worker",
                                 geography_required=True, market_required=True, bucket="Target"),
    RECRUITER: LanePolicy(RECRUITER, "US", "$", unknown_date_blocks=False),
}


# (company substring, title regex), case-insensitive: such a role is REVIEW, not EXCLUDE, for Fit.
KEEP_LIST = (
    ("revolut", r"^product designer \(platform\)$"),
    ("revolut", r"^partnerships manager \(lifestyle\)$"),
    ("revolut", r"^operations manager \(revenue\)$"),
    ("revolut", r"^product owner\s*[(\-\u2013,:]*\s*technical\b"),
    ("revolut", r"^product owner\s*[(\-\u2013,:]*\s*(?:ux|user experience)\b"),
    ("revolut", r"^credit manager\b.*\bproduct\b"),
)

KEEP_OVERRIDES_EXCLUSION = (
    ("revolut", r"^credit manager\b.*\bproduct\b"),
)


TITLE_WATCH_PREFIX = "web:titlewatch:"
TITLE_ONLY_REASON = "found by title search; description not read"
LISTING_REASON = "found by web search; listing page not read"
LISTING_PROOF = "listing"
THIN_REASON = "description too short to judge; Review from its apply link"


def from_listing(link_proof):
    return link_proof == LISTING_PROOF


def title_watch_source(source):
    return bool(source) and source.startswith(TITLE_WATCH_PREFIX)


THIN_REVIEW_PREFIXES = ("web:su-", "web:gh-", "web:lv-", "web:ab-")


def thin_review_source(source):
    """True for a Scale-Up sponsor's own board or a Greenhouse, Lever or Ashby board (thin roles stay open)."""
    return bool(source) and source.startswith(THIN_REVIEW_PREFIXES)


def _listed(table, company, title):
    return any(c in (company or "").lower() and re.search(t, (title or "").strip(), re.I) for c, t in table)


def on_keep_list(company, title):
    return _listed(KEEP_LIST, company, title)


def keep_overrides_exclusion(company, title):
    return _listed(KEEP_OVERRIDES_EXCLUSION, company, title)


@dataclass
class Facts:
    fit: int | None
    market: str | None = None
    work_mode: str = "unknown"
    work_evidence: str | None = None
    pay_min: int | None = None
    pay_currency: str | None = None
    posted: date | None = None
    closed: bool = False
    route: dict = field(default_factory=dict)
    geography: str = UNRESOLVED
    located: bool = False
    kept: bool = False
    thin_admit: bool = False
    thin: bool = False
    title_only: bool = False
    listing: bool = False
    first_party: bool = False  # posting age is not judged
    open_application: bool = False


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str | None = None


def qualify(policy, facts, today):
    """-> Decision; a near-miss Fit with any other unresolved evidence is EXCLUDE, not REVIEW."""
    decision = _qualify(policy, facts, today)
    if decision.status == REVIEW and facts.fit is not None and facts.fit < NEAR_MISS_FLOOR and not (decision.reason or "").startswith("Fit ") and not facts.kept:
        return Decision(EXCLUDE, f"Fit {facts.fit} below {NEAR_MISS_FLOOR} and {decision.reason}")
    if facts.title_only and decision.status == ADMIT:
        return Decision(REVIEW, TITLE_ONLY_REASON)
    if facts.listing and decision.status == ADMIT:
        return Decision(REVIEW, LISTING_REASON)
    if facts.thin and not facts.thin_admit and decision.status == ADMIT:
        return Decision(REVIEW, THIN_REASON)
    return decision


def _qualify(policy, facts, today):
    if not policy.enabled:
        return Decision(DISABLED, "lane disabled")
    if facts.open_application:
        return Decision(EXCLUDE, "not a vacancy: open application")
    if facts.market and facts.market != policy.market:
        return Decision(EXCLUDE, f"market {facts.market} is not {policy.market}")
    if not facts.market and (policy.market_required or (facts.first_party and policy.market == "US")):
        return Decision(REVIEW, "market unresolved")
    if facts.closed:
        return Decision(EXCLUDE, "vacancy closed")
    if facts.fit is not None and facts.fit < REVIEW_FLOOR and not facts.kept:
        return Decision(EXCLUDE, f"Fit {facts.fit} below {REVIEW_FLOOR}")
    if policy.work_mode == "remote_only":
        if facts.work_mode in ("onsite", "hybrid"):
            return Decision(EXCLUDE, f"not remote: {facts.work_mode} required" + (f' ("{facts.work_evidence[:40]}")' if facts.work_evidence else ""))
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
    elif policy.max_age_days is not None and policy.first_party_age and facts.posted is not None and (today - facts.posted).days > policy.max_age_days:
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
    if facts.fit < FIT_FLOOR and not (facts.thin_admit and facts.fit >= REVIEW_FLOOR):
        return Decision(REVIEW, f"Fit {facts.fit} below {FIT_FLOOR}" + (" (on Jim's keep list)" if facts.kept else ""))
    return Decision(ADMIT, "remote assumed" if policy.work_mode == "remote_only" and facts.work_mode == "unknown" else None)


def qualify_all(facts, today, policies=POLICIES, own=None):
    """({lane: Decision} for every enabled lane, visible lane or None)."""
    results = {name: qualify(p, facts, today) for name, p in policies.items() if p.enabled}
    if own is not None:
        for name, p in policies.items():
            if name != own and name in results and results[name].status == ADMIT and facts.market != p.market:
                results[name] = Decision(EXCLUDE, f"market not shown to be {p.market}")
    visible = next((n for n in PRECEDENCE if n in results and results[n].status == ADMIT), None)
    return results, visible


_PAY = re.compile(r"([$£€])\s?(\d[\d,]*(?:\.\d+)?)\s?(k|m)?\b(?:\s*(?:/|per|a)\s*(yr|year|annum|hr|hour))?", re.I)


def parse_pay(text):
    """(annual minimum, currency symbol) from posted pay text, or (None, None); hourly is x2,080."""
    best = None
    for symbol, number, scale, unit in _PAY.findall(text or ""):
        value = float(number.replace(",", ""))
        value *= {"k": 1_000, "m": 1_000_000}.get((scale or "").lower(), 1)
        if (unit or "").lower() in ("hr", "hour"):
            value *= 2080
        elif not scale and not unit and value < 1000:
            continue
        best = (int(value), symbol) if best is None or value < best[0] else best
    return best if best else (None, None)


_NEGATED = re.compile(r"\b(?:not|no|never|isn't|aren't|without|optional|optionally|occasional|occasionally|if you prefer|flexibility to)\b[^.;\n]{0,30}$", re.I)
_HYBRID_REQ = re.compile(r"\bhybrid (?:role|work|schedule|position|model|arrangement|working)\b|"
                         r"\b(?:\d|one|two|three|four)\s+days?\s+(?:a|per|each)\s+week\s+(?:in|at|from)\s+(?:the |our |an? )?(?:office|hq|headquarters|studio|campus)\b|"
                         r"\b(?:in[- ]office|on-?site)\s+(?:\d|one|two|three|four)\s+days?\b", re.I)
_HYBRID_DAYS = re.compile(r"\b(?:in|at|from)\s+(?:the |our |an? )?(?:[a-z]+ )?(?:office|hq|headquarters|studio|campus)s?\s+(?:at least |a minimum of )?(?:\d\+?|one|two|three|four)\+?\s+days?\b|"
                          r"\b(?:\d\+?|one|two|three|four)\+?\s+days?\s+(?:a|per|each)\s+week\s+(?:in|at|from)\s+(?:the |our |an? )?(?:[a-z]+ )?(?:office|hq|headquarters|studio|campus)\b", re.I)
_ONSITE_REQ = re.compile(r"\b(?:must|required to|requires you to|expected to|will need to|need to)\s+(?:be\s+|work\s+|report\s+)?(?:on-?site|in[- ]office|in (?:the|our) office|(?:from|at|in) (?:the|our) (?:office|hq|headquarters|campus))\b|"
                         r"\bthis (?:is an? |role is |position is )(?:an? )?(?:on-?site|in[- ]office|office[- ]based)\b|"
                         r"\b(?:fully|100%|five days)\s+(?:on-?site|in[- ]office|in the office)\b|\b(?:on-?site|in[- ]office|office[- ]based)\s+(?:role|position|job)\b|"
                         r"\b(?:not|isn't) (?:a )?remote (?:role|position|job)\b|\bremote work is not (?:available|possible|offered)\b", re.I)
_REMOTE_STATED = re.compile(r"\b(?:fully|100%|completely) remote\b|\b(?:remote|virtual)[- ]first\b|\bwork from anywhere\b|\bthis is a remote (?:role|position)\b|"
                            r"\bremote\b[^.\n]{0,40}\b(?:based|located|living|anywhere) in (?:the )?(?:continental )?(?:us|u\.s\.|usa|united states)\b|"
                            r"\bwork (?:from )?anywhere in the (?:us|u\.s\.|united states)\b", re.I)


_CONDITIONAL = re.compile(r"\b(?:if you|for (?:candidates|those|employees|team members) (?:who|located|based|living|near|in|within)|local (?:candidates|employees)|"
                          r"within \d+ miles|when (?:needed|required|necessary)|we offer|you (?:can|may) (?:choose|opt)|(?:the )?option to)\b", re.I)
_OCCASIONAL = re.compile(r"^[^.;\n]{0,25}?\b(?:occasional(?:ly)?|as needed|when needed|if needed|periodically|for (?:quarterly|annual|team|company|occasional|key)\b|"
                         r"(?:once|twice) a (?:month|quarter|year)|a few times a (?:year|quarter)|at (?:a )?(?:customer|client)|with (?:customers|clients)|"
                         r"for (?:those|anyone|people) who (?:want|prefer|choose|like)|if (?:you )?(?:prefer|wish|want|desired))", re.I)


def _required(pattern, text):
    for m in pattern.finditer(text):
        sentence = re.split(r"[.;\n]", text[:m.start()])[-1]
        if not (_NEGATED.search(text[max(0, m.start() - 40):m.start()]) or _CONDITIONAL.search(sentence) or _OCCASIONAL.search(text[m.end():])):
            return m.group(0)
    return None


def work_mode_evidence(location, title="", text="", window=1500):
    """(remote | hybrid | onsite | unknown, the employer's own words or None); attendance beats remote cues."""
    head = f"{location or ''} {title or ''}"
    body = (text or "")[:window]
    for mode, pattern in (("hybrid", _HYBRID_REQ), ("onsite", _ONSITE_REQ)):
        found = _required(pattern, body)
        if found:
            return mode, found
    found = _required(_HYBRID_DAYS, text or "")
    if found:
        return "hybrid", found
    tag = re.search(r"#li-(hybrid|onsite)\b", text or "", re.I)
    if tag:
        return tag.group(1).lower(), tag.group(0)
    if re.search(r"\bremote\b|\bwork from home\b|\banywhere\b", head, re.I):
        return "remote", None
    for mode, rx in (("hybrid", r"\bhybrid\b"), ("onsite", r"\bon-?site\b|\bin[- ]office\b")):
        hit = re.search(rx, head, re.I)
        if hit and not _NEGATED.search(head[:hit.start()][-40:]):
            return mode, hit.group(0)
    if _REMOTE_STATED.search(body) or re.search(r"#li-remote\b", text or "", re.I):
        return "remote", None
    return "unknown", None


def detect_work_mode(location, title="", text="", window=1500):
    return work_mode_evidence(location, title, text, window)[0]


LANE_ALIAS = {"Newsletter": "US Remote"}
ADMISSION_LABEL = {ADMIT: "Admitted", REVIEW: "Passed / Review", EXCLUDE: "Excluded"}
POLICY_VERSION = "l17"


def lane_for(row_lane):
    return LANE_ALIAS.get(row_lane or "Newsletter", row_lane or "US Remote")


_UK_REMOTE = re.compile(r"remote\W{0,6}(?:uk|u\.k\.|united kingdom|england|great britain|gb)\b|\b(?:uk|united kingdom|england|great britain)\W{0,6}remote")


def geography_status(location):
    """London is POSITIVE, a named other place NEGATIVE, anything else UNRESOLVED."""
    text = (location or "").casefold()
    if not text:
        return UNRESOLVED
    if "london" in text or _UK_REMOTE.search(text):
        return POSITIVE
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
    """'US' | 'UK' | 'OTHER' from the location text; None when silent, ambiguous or several."""
    text = location or ""
    found = []
    if _CANADA.search(text) or _OTHER.search(text):
        found.append("OTHER")
    if _UK.search(text) and not _CANADA.search(text):  # "London, ON" is Canada
        found.append("UK")
    if _US.search(text):
        found.append("US")
    return found[0] if len(found) == 1 else None


def route_dict(stored):
    """'Scale-up:POSITIVE;Skilled Worker:NEGATIVE' -> {'Scale-up': 'POSITIVE', ...}."""
    out = {}
    for part in (stored or "").split(";"):
        name, _, state = part.partition(":")
        if name.strip() and state.strip():
            out[name.strip()] = state.strip()
    return out


def join_routes(stored, **extra):
    merged = {**route_dict(stored), **extra}
    return ";".join(f"{k}:{v}" for k, v in merged.items())


FIT_WINDOW = 6000


def work_mode_for_fit(location, title, text):
    return detect_work_mode(location, title, text, window=FIT_WINDOW)


def first_party_age(lane):
    """The lane's max age when employer-board jobs are judged by age, else None."""
    name = lane_for(lane)
    policy = next((p for key, p in POLICIES.items() if key.casefold() == name.casefold()), None)
    return policy.max_age_days if policy and policy.first_party_age else None


def first_party_source(source):
    """True for a job read off an employer's own board (source web:<id>), not a feed or aggregator."""
    return bool(source) and source.startswith("web:") and source != "web:openjobs"


US_PLACES = re.compile(r"(?i:\b(?:nyc|new york|san francisco|bay area|los angeles|chicago|seattle|boston|austin|denver|atlanta|san jose|houston|phoenix|"
                       r"philadelphia|san diego|dallas|fort worth|portland|sunnyvale|mountain view|palo alto|redwood city|menlo park|boise|salt lake city|miami|"
                       r"fort lauderdale|jacksonville|charlotte|raleigh|nashville|minneapolis|pittsburgh|detroit|columbus|cincinnati|indianapolis|baltimore|"
                       r"alpharetta|tampa|orlando|las vegas|sacramento|oakland|irvine|santa monica|boulder|omaha|tucson|albuquerque|honolulu|st\.? louis|"
                       r"kansas city|new orleans|milwaukee|washington,? d\.?c\.?|district of columbia|alabama|alaska|arizona|arkansas|california|colorado|"
                       r"connecticut|delaware|florida|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|"
                       r"minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|north carolina|north dakota|ohio|oklahoma|"
                       r"oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|"
                       r"wyoming)\b)|\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|"
                       r"SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC) - ")
_US_ELIGIBLE = re.compile(r"(?i:\b(?:based|located|living|resid\w*|work\w*)\s+(?:anywhere\s+)?(?:in|within)\s+the\s+(?:continental\s+)?(?:u\.s\.a?|usa|united states)\b|"
                          r"\bauthori[sz]ed to work in the (?:u\.s\.|united states)\b)|\b(?:based|located|living|resid\w*|work\w*)\s+(?:anywhere\s+)?(?:in|within)\s+"
                          r"the\s+(?:continental\s+)?US\b|\bU\.?S\.?[- ]based\b|\bauthori[sz]ed to work in the US\b")


_OPEN_APPLICATION = re.compile(r"\b(?:initiativ|spontan|blind)bewerbung\b|\b(?:open|unsolicited|speculative|general|spontaneous) application\b|"
                               r"\btalent (?:pool|community|network)\b|\bcandidature spontan[ée]e\b|\bfuture opportunities\b", re.I)


def open_application(title):
    return bool(_OPEN_APPLICATION.search(title or ""))


def facts_for(fit, title, location, text, salary_text, posted, first_seen, market=None, route=None, first_party=False, kept=False, title_only=False, thin=False, thin_admit=False, listing=False):
    """Facts for one stored job; age uses the posting date, else first-seen."""
    pay_min, currency = parse_pay(salary_text)
    when = posted or (first_seen.date() if hasattr(first_seen, "date") else first_seen)
    mode, evidence = work_mode_evidence(location, title, text, window=FIT_WINDOW)
    market = market or market_of(location)
    if first_party and not market and (US_PLACES.search(location or "") or _US_ELIGIBLE.search(text or "")):
        market = "US"
    return Facts(fit=fit, market=market, work_mode=mode, work_evidence=evidence, pay_min=pay_min,
                 pay_currency=currency, posted=when, route=route_dict(route), geography=geography_status(location), located=bool((location or "").strip()), first_party=first_party, kept=kept, title_only=title_only, listing=listing, thin=thin, thin_admit=thin and thin_admit,
                 open_application=open_application(title))


def decide_all(row_lane, facts, today, exclusion=None):
    """-> (Decision, visible lane, eligible lanes); the own lane judges first, PRECEDENCE breaks ties, a private
    exclusion wins.
    """
    own = lane_for(row_lane)
    if exclusion:
        return Decision(EXCLUDE, f"excluded: {exclusion}"), own, []
    if own == RECRUITER:
        return Decision(ADMIT, "recruiter outreach: admitted without the Fit gate"), RECRUITER, [RECRUITER]
    results, visible = qualify_all(facts, today, own=own)
    eligible = [n for n in PRECEDENCE if n in results and results[n].status == ADMIT]
    if own in results and results[own].status == ADMIT:
        lane = visible if visible and PRECEDENCE.index(visible) < PRECEDENCE.index(own) else own
        return results[lane], lane, eligible
    if visible:
        return results[visible], visible, eligible
    return qualify(POLICIES[own], facts, today), own, eligible


def decide(row_lane, facts, today, exclusion=None):
    decision, lane, _ = decide_all(row_lane, facts, today, exclusion)
    return decision, lane


_N = r"\d+(?:\.\d+)?"
PUBLIC_REASONS = (
    (rf"Fit {_N} below {NEAR_MISS_FLOOR} and .+", "fit_below_near_miss_with_open_question"),
    (rf"Fit {_N} below {REVIEW_FLOOR}", "fit_below_review_floor"),
    (rf"Fit {_N} below {FIT_FLOOR}", "fit_below_admit_floor"),
    (rf"Fit {_N} below {FIT_FLOOR} \(on Jim's keep list\)", "fit_below_admit_floor_keep_list"),
    (r"not remote: hybrid required(?: \(.*\))?", "not_remote_hybrid_required"),
    (r"not remote: onsite required(?: \(.*\))?", "not_remote_onsite_required"),
    (r"remote assumed", "remote_assumed"), (r"Fit unscorable", "fit_unscorable"), (r"vacancy closed", "vacancy_closed"), (r"not a vacancy: open application", "not_a_vacancy"),
    (r"explicit pay below .+", "pay_below_floor"), (r"posting date unresolved", "posting_date_unresolved"),
    (r"posting date in the future", "posting_date_future"), (rf"older than {_N} days", "older_than_max_age"),
    (r"market unresolved", "market_unresolved"), (r"market .+ is not .+", "market_mismatch"), (r"market not shown to be .+", "market_not_shown"),
    (r"geography unresolved", "geography_unresolved"), (r"geography does not qualify", "geography_not_qualifying"), (r"not in London", "not_in_london"),
    (r".+ route negative", "route_negative"), (r".+ route unresolved", "route_unresolved"), (r"lane disabled", "lane_disabled"),
    (re.escape(TITLE_ONLY_REASON), "title_only"), (re.escape(LISTING_REASON), "listing_only"), (re.escape(THIN_REASON), "thin_description"), (r"excluded: .*", "excluded_private_rule"), (rf"Fit {_N} below {_N}", "fit_below_floor"))


def public_reason(reason):
    """A fixed code from PUBLIC_REASONS, "none" for no reason, "other" otherwise."""
    if not reason:
        return "none"
    return next((code for pattern, code in PUBLIC_REASONS if re.fullmatch(pattern, reason, re.S)), "other")
