"""Lane policy: one data-driven qualification for every acquisition lane (docs/LANES.md).

Professional Fit is shared and lives in lifeos.jobs.fit (floor 72 for every lane, D191; below it a job goes to Review, D81). A lane only adds opportunity policy:
market, work mode, explicit pay, posting age, route and geography evidence. Unresolved evidence is REVIEW, never guessed;
definitive negative evidence (and a closed vacancy) is EXCLUDE.
"""
from dataclasses import dataclass, field
from datetime import date
import re

from lifeos.jobs.fit import UNIVERSAL_FIT_FLOOR

FIT_FLOOR = UNIVERSAL_FIT_FLOOR          # D191: ADMIT needs Fit 72 in every lane; below it the job is stored as Review (or Exclude by the rules below), never admitted
NEAR_MISS_FLOOR = 68                    # D100: unchanged. A Fit under this with a second open question is Exclude, not Review
REVIEW_FLOOR = 60                       # D84: below 60 is excluded
POSITIVE, NEGATIVE, UNRESOLVED = "POSITIVE", "NEGATIVE", "UNRESOLVED"
ADMIT, REVIEW, EXCLUDE, DISABLED = "ADMIT", "REVIEW", "EXCLUDE", "DISABLED"
RECRUITER = "Recruiter"          # LIFE-88 E01b: a recruiter's outreach. Not an acquisition lane: it takes no part in the visible-lane precedence, and decide_all admits it without the Fit gate
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
    # non-target place negative, UK-remote / unresolved geography goes to Review; explicit pay under GBP 65,000 excludes; 14 days; Fit 72 like every lane.
    "Skilled Worker": LanePolicy("Skilled Worker", "UK", "£", pay_floor=65_000, max_age_days=14, route="Skilled Worker",
                                 geography_required=True, market_required=True, bucket="Target"),
    RECRUITER: LanePolicy(RECRUITER, "US", "$", unknown_date_blocks=False),          # no gate of its own (decide_all); present so freshness and hold lookups find a policy
}


# D115: roles Jim chose to see whatever the model scores. (company substring, title regex), both case-insensitive. A listed role that no hard exclusion rule
# hits and that clears every other lane test is never excluded for Fit: a score under 72 is Review ("Fit N below 72 (on Jim's keep list)"), so it reaches the
# board for Jim to decide. The score itself is stored untouched.
KEEP_LIST = (
    ("revolut", r"^product designer \(platform\)$"),
    ("revolut", r"^partnerships manager \(lifestyle\)$"),
    ("revolut", r"^operations manager \(revenue\)$"),
    # Jim, Oct 20: four Revolut roles that must reach the board. Operations Manager (Revenue) is listed above; the title patterns tolerate the usual punctuation variants.
    ("revolut", r"^product owner\s*[(\-\u2013,:]*\s*technical\b"),
    ("revolut", r"^product owner\s*[(\-\u2013,:]*\s*(?:ux|user experience)\b"),
    ("revolut", r"^credit manager\b.*\bproduct\b"),
)

# A keep-list role that a private TITLE rule would exclude (Credit Manager matches the financial-risk rule on "credit manager", but this one is a product role).
# Jim chose it by name, so the rule does not exclude it; its Fit is scored as usual and it reaches the board as Review. Every other keep-list role still obeys the rules.
KEEP_OVERRIDES_EXCLUSION = (
    ("revolut", r"^credit manager\b.*\bproduct\b"),
)


TITLE_WATCH_PREFIX = "web:titlewatch:"
TITLE_ONLY_REASON = "found by title search; description not read"


def title_watch_source(source):
    return bool(source) and source.startswith(TITLE_WATCH_PREFIX)


def on_keep_list(company, title):
    return any(c in (company or "").lower() and re.search(t, (title or "").strip(), re.I) for c, t in KEEP_LIST)


def keep_overrides_exclusion(company, title):
    return any(c in (company or "").lower() and re.search(t, (title or "").strip(), re.I) for c, t in KEEP_OVERRIDES_EXCLUSION)


@dataclass
class Facts:
    fit: int | None                      # professional Fit, None = unscorable
    market: str | None = None            # "US" | "UK" | None (unknown)
    work_mode: str = "unknown"           # remote | hybrid | onsite | unknown
    work_evidence: str | None = None     # D204: the employer's words that require office attendance
    pay_min: int | None = None           # annual minimum, in `pay_currency`, only when explicit
    pay_currency: str | None = None
    posted: date | None = None
    closed: bool = False                 # definitive removal / closure evidence
    route: dict = field(default_factory=dict)   # {"Scale-up": POSITIVE, ...}
    geography: str = UNRESOLVED
    located: bool = False                # the job states a place at all
    kept: bool = False                   # D115: on Jim's keep list (or found by a title watch: Jim chose the title)
    title_only: bool = False             # D123: found by a title search; the page text could not be read, so the Fit rests on the title alone
    first_party: bool = False            # D111: listed on the employer's own board, so it is open: its posting age is not judged
    open_application: bool = False       # D213: an unsolicited / general application ("Initiativbewerbung", "Talent Pool"): not a vacancy


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str | None = None


def qualify(policy, facts, today):
    """D100: Review is for ONE open question on a job that otherwise clearly fits. A near-miss Fit (60-67) with any other evidence also unresolved
    (work mode, posting date, route, geography) is not Review, it is EXCLUDE: two doubts on a weak fit is a miss, not a question for Jim."""
    decision = _qualify(policy, facts, today)
    if decision.status == REVIEW and facts.fit is not None and facts.fit < NEAR_MISS_FLOOR and not (decision.reason or "").startswith("Fit ") and not facts.kept:
        return Decision(EXCLUDE, f"Fit {facts.fit} below {NEAR_MISS_FLOOR} and {decision.reason}")
    if facts.title_only and decision.status == ADMIT:                  # D123: a score from a title alone is never a Go; Jim decides from the link
        return Decision(REVIEW, TITLE_ONLY_REASON)
    return decision


def _qualify(policy, facts, today):
    if not policy.enabled:
        return Decision(DISABLED, "lane disabled")
    if facts.open_application:
        return Decision(EXCLUDE, "not a vacancy: open application")
    if facts.market and facts.market != policy.market:
        return Decision(EXCLUDE, f"market {facts.market} is not {policy.market}")
    if not facts.market and (policy.market_required or (facts.first_party and policy.market == "US")):     # M5: an employer board lists the world
        return Decision(REVIEW, "market unresolved")
    if facts.closed:
        return Decision(EXCLUDE, "vacancy closed")
    if facts.fit is not None and facts.fit < REVIEW_FLOOR and not facts.kept:
        return Decision(EXCLUDE, f"Fit {facts.fit} below {REVIEW_FLOOR}")
    if policy.work_mode == "remote_only":
        if facts.work_mode in ("onsite", "hybrid"):                    # D204: only a requirement excludes; its words are the evidence
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
        return Decision(REVIEW, f"Fit {facts.fit} below {FIT_FLOOR}" + (" (on Jim's keep list)" if facts.kept else ""))
    return Decision(ADMIT, "remote assumed" if policy.work_mode == "remote_only" and facts.work_mode == "unknown" else None)     # D204: kept apart from stated remote


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


# D204 (Jim, Oct 17): a US job is presumed workable remotely unless the employer REQUIRES office attendance. Only mandatory statements count;
# a negated or optional mention ("no onsite requirement", "optional office access", "occasional travel to our office") is not a requirement.
_NEGATED = re.compile(r"\b(?:not|no|never|isn't|aren't|without|optional|optionally|occasional|occasionally|if you prefer|flexibility to)\b[^.;\n]{0,30}$", re.I)
_HYBRID_REQ = re.compile(r"\bhybrid (?:role|work|schedule|position|model|arrangement|working)\b|"
                         r"\b(?:\d|one|two|three|four)\s+days?\s+(?:a|per|each)\s+week\s+(?:in|at|from)\s+(?:the |our |an? )?(?:office|hq|headquarters|studio|campus)\b|"
                         r"\b(?:in[- ]office|on-?site)\s+(?:\d|one|two|three|four)\s+days?\b", re.I)
_ONSITE_REQ = re.compile(r"\b(?:must|required to|requires you to|expected to|will need to|need to)\s+(?:be\s+|work\s+|report\s+)?(?:on-?site|in[- ]office|in (?:the|our) office|(?:from|at|in) (?:the|our) (?:office|hq|headquarters|campus))\b|"
                         r"\bthis (?:is an? |role is |position is )(?:an? )?(?:on-?site|in[- ]office|office[- ]based)\b|"
                         r"\b(?:fully|100%|five days)\s+(?:on-?site|in[- ]office|in the office)\b|\b(?:on-?site|in[- ]office|office[- ]based)\s+(?:role|position|job)\b|"
                         r"\b(?:not|isn't) (?:a )?remote (?:role|position|job)\b|\bremote work is not (?:available|possible|offered)\b", re.I)
_REMOTE_STATED = re.compile(r"\b(?:fully|100%|completely) remote\b|\b(?:remote|virtual)[- ]first\b|\bwork from anywhere\b|\bthis is a remote (?:role|position)\b|"
                            r"\bremote\b[^.\n]{0,40}\b(?:based|located|living|anywhere) in (?:the )?(?:continental )?(?:us|u\.s\.|usa|united states)\b|"
                            r"\bwork (?:from )?anywhere in the (?:us|u\.s\.|united states)\b", re.I)


# Not a requirement for everyone: a condition earlier in the same sentence ("if you live near our Chicago office, ...") or a qualifier right after
# the words ("... onsite for quarterly planning", "... in the office occasionally", "... onsite at customer sites").
_CONDITIONAL = re.compile(r"\b(?:if you|for (?:candidates|those|employees|team members) (?:who|located|based|living|near|in|within)|local (?:candidates|employees)|"
                          r"within \d+ miles|when (?:needed|required|necessary)|we offer|you (?:can|may) (?:choose|opt)|(?:the )?option to)\b", re.I)
_OCCASIONAL = re.compile(r"^[^.;\n]{0,25}?\b(?:occasional(?:ly)?|as needed|when needed|if needed|periodically|for (?:quarterly|annual|team|company|occasional|key)\b|"
                         r"(?:once|twice) a (?:month|quarter|year)|a few times a (?:year|quarter)|at (?:a )?(?:customer|client)|with (?:customers|clients)|"
                         r"for (?:those|anyone|people) who (?:want|prefer|choose|like)|if (?:you )?(?:prefer|wish|want|desired))", re.I)


def _required(pattern, text):
    """The first mandatory (not negated, not optional, not conditional) match of `pattern` in `text`, else None."""
    for m in pattern.finditer(text):
        sentence = re.split(r"[.;\n]", text[:m.start()])[-1]
        if not (_NEGATED.search(text[max(0, m.start() - 40):m.start()]) or _CONDITIONAL.search(sentence) or _OCCASIONAL.search(text[m.end():])):
            return m.group(0)
    return None


def work_mode_evidence(location, title="", text="", window=1500):
    """(remote | hybrid | onsite | unknown, the employer's own words or None). "unknown" means nothing is required either way: remote is presumed
    (D204), never stated. A mandatory attendance requirement wins over any remote cue it contradicts (M3 review): first a required hybrid schedule
    or office attendance in the description, then the recruiter's #LI-Hybrid/#LI-Onsite tag (read anywhere: it is usually the last line); then
    the employer's place/title (remote wins when offered alongside hybrid); an explicit remote statement; #LI-Remote."""
    head = f"{location or ''} {title or ''}"
    body = (text or "")[:window]
    for mode, pattern in (("hybrid", _HYBRID_REQ), ("onsite", _ONSITE_REQ)):
        found = _required(pattern, body)
        if found:
            return mode, found
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
    """remote | hybrid | onsite | unknown (work_mode_evidence without the words)."""
    return work_mode_evidence(location, title, text, window)[0]


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
    """detect_work_mode over the Fit window (D92). D89's rule that a named US city and state with no cue is an office job is withdrawn by D204."""
    return detect_work_mode(location, title, text, window=FIT_WINDOW)      # D204: a named city with no office requirement is no longer read as office


def first_party_source(source):
    """True for a job read off an employer's own board by the web pass (source web:<id>), not off the Open Jobs feed or an aggregator (D111)."""
    return bool(source) and source.startswith("web:") and source != "web:openjobs"


# US places and statements that show US eligibility (M5 review). Used for an employer-board job only, where the board lists the whole world: its
# market is US when its place names a US city or state or the employer says so; remote alone is not US eligibility.
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


# D213: titles that invite an application with no vacancy behind it. Judged on the title only: a posting that merely mentions a talent pool is still a job.
_OPEN_APPLICATION = re.compile(r"\b(?:initiativ|spontan|blind)bewerbung\b|\b(?:open|unsolicited|speculative|general|spontaneous) application\b|"
                               r"\btalent (?:pool|community|network)\b|\bcandidature spontan[ée]e\b|\bfuture opportunities\b", re.I)


def open_application(title):
    return bool(_OPEN_APPLICATION.search(title or ""))


def facts_for(fit, title, location, text, salary_text, posted, first_seen, market=None, route=None, first_party=False, kept=False, title_only=False):
    """Facts for one stored job. Age uses the employer Posting Date, else First Surfaced (never a crawl time invented as a
    posting date). Pay comes only from the posted pay field."""
    pay_min, currency = parse_pay(salary_text)
    when = posted or (first_seen.date() if hasattr(first_seen, "date") else first_seen)
    mode, evidence = work_mode_evidence(location, title, text, window=FIT_WINDOW)
    market = market or market_of(location)
    if first_party and not market and (US_PLACES.search(location or "") or _US_ELIGIBLE.search(text or "")):
        market = "US"
    return Facts(fit=fit, market=market, work_mode=mode, work_evidence=evidence, pay_min=pay_min,
                 pay_currency=currency, posted=when, route=route_dict(route), geography=geography_status(location), located=bool((location or "").strip()), first_party=first_party, kept=kept, title_only=title_only,
                 open_application=open_application(title))


def decide_all(row_lane, facts, today, exclusion=None):
    """-> (Decision, visible lane, eligible lanes). The job's own lane judges it first; when another enabled lane admits it and its own does not
    (a UK newsletter job at a licensed sponsor), that lane takes it. A lane that already admits it keeps it unless a lane that precedes it also
    admits (Scale-Up wins a dual route). A private exclusion rule excludes in every lane, with the Fit untouched."""
    own = lane_for(row_lane)
    if exclusion:
        return Decision(EXCLUDE, f"excluded: {exclusion}"), own, []
    if own == RECRUITER:                                              # Jim: a recruiter wrote to him, so the job is shown whatever its Fit; the score is still stored and shown
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
    """-> (Decision, lane name)."""
    decision, lane, _ = decide_all(row_lane, facts, today, exclusion)
    return decision, lane


# Public reasons (M3 review): a lane reason as a fixed code a public log may print; boards-preview, funnel and explain use it.
_N = r"\d+(?:\.\d+)?"
PUBLIC_REASONS = (                                       # (lane reason, the fixed code a public log may print); nothing of the reason is copied
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
    (re.escape(TITLE_ONLY_REASON), "title_only"), (r"excluded: .*", "excluded_private_rule"), (rf"Fit {_N} below {_N}", "fit_below_floor"))     # last: an older floor


def public_reason(reason):
    """A preview reason fit for a public log: one of the fixed codes in PUBLIC_REASONS, "none" for no reason, and "other" for anything not
    enumerated there. No rule name, profile content, employer quotation or exception text is ever copied."""
    if not reason:
        return "none"
    return next((code for pattern, code in PUBLIC_REASONS if re.fullmatch(pattern, reason, re.S)), "other")
