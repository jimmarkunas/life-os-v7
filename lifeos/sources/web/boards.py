"""D202: the employer-board pass. About 10,700 Greenhouse, Ashby and Lever boards (the Open Jobs board list, CC0, plus the extras in
boards.json) read through the same web run as the priority boards: the same states, diff, FAILED-is-never-a-removal rule and admission.
Differences, all bounded: more boards per run on fewer workers, a slower refresh unless the board lists a target job, a smaller admission
budget, only shown targets stored (`screen` names every other job's reason; a job screened out while its employer still lists it is not
a removal), and an ATS that answers 429 three times is not read
again this run (its boards are FAILED `skipped_after_rate_limit_429` and retried later)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os
import random
import re
import threading

from lifeos.jobs import lanes
from lifeos.platform import limits
from lifeos.sources.web import lister, registry, run as web, suppress

LANE = "US Remote"
# The role screen (D202 review): pass every plausible role to the shared Fit, which holds the private profile, and name every title it does not pass.
# Strong: the role itself is program, project, product, delivery, implementation, transformation or solutions leadership, whatever its domain word.
STRONG = re.compile(r"\b(program(?:me)? (?:manag\w*|lead|director|head)|(?:technical|engineering|product|ai|data|security|it|infrastructure|platform) program(?:me)?s?\b|"
                    r"tpm|project (?:manag\w*|lead|director)|delivery (?:manag\w*|lead|director|head)|head of (?:delivery|programs?|programmes?|product|pmo|transformation)|"
                    r"implementation (?:manag\w*|lead|director|consultant)|product (?:manag\w*|lead|owner|director|head)|(?:director|vp|vice president|head) of product(?! marketing| design)|"
                    r"chief of staff|transformation (?:lead|manag\w*|director|head|program)|pmo|engagement manager|solutions? (?:architect|consultant)|portfolio (?:manag\w*|lead|director)|"
                    r"release manag\w*)\b", re.I)
# Adjacent: operations, strategy and transformation leadership, plausible only when no other profession or junior support word is in the title.
# Product Marketing is NOT passed (Fit audit, Oct 17: Admitted Product Marketing scored up to 100 with "Gaps: none" on generic words such as
# Cloud/SaaS; Fit cannot tell it from the target family yet, so passing it would admit it).
ADJACENT = re.compile(r"\b((?:business|strategy (?:and|&)|strategic|technical) operations|(?:director|vp|vice president|head|chief) of (?:operations|strategy)|"
                      r"operations (?:director|lead|manager|head)|strategy (?:and|&) operations|transformation|(?:director|head|vp|vice president)\b[^,]{0,25}\bstrategy|"
                      r"head of (?:technology|engineering) (?:&|and) product)\b", re.I)
NOT_ROLE = re.compile(r"\b(coordinator|assistant|specialist|representative|designer|engineer|developer|scientist|marketing|sales|account executive|recruit\w*|"
                      r"accountant|accounting|payroll|nurse|clinical|technician|driver|analyst|associate|clerk|customer (?:success|support|service)|finance|financial|"
                      r"legal|counsel|people|hr|talent|facilities|warehouse|construction|revenue|franchise|underwriting|officer)\b", re.I)
NOT_A_ROLE = re.compile(r"\b(intern|internship|coach|mentor|tutor|instructor|teacher|future (?:tech )?positions?)\b", re.I)


def role(title):
    """-> (tier, None) for a plausible role ("strong" or "adjacent"), else (None, reason)."""
    out = NOT_A_ROLE.search(title)
    if out:
        return None, "not_a_role_" + out.group(1).lower().split()[0]                  # coaching, internships, talent-pool posts
    if STRONG.search(title):
        return "strong", None
    hit = NOT_ROLE.search(title)
    if ADJACENT.search(title) and not hit:
        return "adjacent", None
    return None, ("other_profession_" + hit.group(1).lower().replace(" ", "_")) if hit else "no_role_term"


US = re.compile(r"united states|\busa?\b|\bu\.s\.", re.I)
REMOTE = re.compile(r"\bremote\b|\banywhere\b", re.I)
PLACELESS = re.compile(r"^\W*(?:fully\W+)?(?:remote|anywhere|worldwide|global)(?:\W+(?:remote|anywhere|worldwide|global))*\W*$", re.I)   # "Remote", "Remote - Anywhere"
WHOLE_US = re.compile(r"^\W*(?:united states(?: of america)?|usa?|u\.s\.(?:a\.)?)\W*$", re.I)                                        # the country, no city


# Geography (D204 review): a place is non-US only when it NAMES somewhere outside the US and nothing in it is US; a US city without a state code,
# or a place that names nothing, is not proof of anything either way: it is kept as `unresolved`, never promoted to verified US.
NON_US_NAMED = re.compile(r"\b(algeria|algiers|egypt|cairo|nigeria|lagos|kenya|nairobi|south africa|morocco|tunisia|saudi|riyadh|dubai|uae|qatar|turkey|istanbul|"
                          r"pakistan|karachi|lahore|bangladesh|dhaka|sri lanka|china|shanghai|beijing|shenzhen|hong kong|taiwan|taipei|korea|seoul|vietnam|hanoi|"
                          r"ho chi minh|thailand|bangkok|indonesia|jakarta|malaysia|kuala lumpur|colombia|bogot[a\u00e1]|argentina|buenos aires|brazil|s[a\u00e3]o paulo|"
                          r"chile|santiago|peru|lima|costa rica|ecuador|nicaragua|uruguay|guatemala|mexico|latam|latin america|emea|apac|europe|asia|africa|middle east|"
                          r"greece|athens|piraeus|lithuania|vilnius|latvia|riga|estonia|tallinn|romania|bucharest|bulgaria|sofia|serbia|belgrade|croatia|zagreb|"
                          r"hungary|budapest|czech|prague|slovakia|slovenia|austria|vienna|switzerland|zurich|geneva|belgium|brussels|luxembourg|frankfurt|"
                          r"düsseldorf|dusseldorf|hamburg|cologne|denmark|copenhagen|sweden|stockholm|norway|oslo|finland|helsinki|italy|milan|rome|"
                          r"noida|gurgaon|gurugram|chennai|delhi|kolkata|phl|nepal|new zealand|auckland)\b", re.I)
US_NAMED = re.compile(r"\b(nyc|new york|san francisco|bay area|los angeles|chicago|seattle|boston|austin|denver|atlanta|alabama|alaska|arizona|arkansas|california|"
                      r"colorado|connecticut|delaware|florida|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|"
                      r"minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|north carolina|north dakota|ohio|oklahoma|oregon|"
                      r"pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming)\b", re.I)


INCLUDES_US = re.compile(r"\b(north america|namer|americas|global|worldwide|anywhere)\b", re.I)     # a region that may include the US: unresolved, never non-US


def geography(where):
    """us | non_us | unresolved, from the place text alone."""
    where = where or ""
    us = US.search(where) or suppress.US.search(where) or WHOLE_US.match(where) or US_NAMED.search(where)
    if not us and INCLUDES_US.search(where):
        return "unresolved"
    if not us and (lanes.market_of(where) in ("UK", "OTHER") or suppress.NON_US.search(where) or NON_US_NAMED.search(where)):
        return "non_us"
    return "us" if us or PLACELESS.match(where) else "unresolved"


def screen(job):
    """One named reason per listed job; a job is stored only when it starts with "kept_". The title must be a plausible role (`role`). D204: a US job is presumed workable remotely unless the employer
    requires office attendance (lanes.detect_work_mode reads only mandatory words); kept jobs say which: remote stated in the place/title or the
    description, or `kept_remote_assumed`. Geography: `geography` (a named non-US place
    excludes; an unnamed one is kept as `_place_unresolved`). Last, the web pass's own cheap suppression (age is no reason)."""
    title, where, text = job["title"], job["location"] or "", job.get("content") or ""
    tier, why = role(title)
    if not tier:
        return "title_" + why
    place = geography(where)
    if place == "non_us":
        return "non_us"
    tail = "_place_unresolved" if place == "unresolved" else ""
    mode = lanes.detect_work_mode(where, title, text)
    if mode in ("onsite", "hybrid"):
        return "not_remote_" + mode                           # D204: only an employer requirement excludes
    why = suppress.reason(job, None, LANE, first_party=True)
    if why:
        return "suppressed_" + why
    if mode == "unknown":                                     # D204: nothing required either way: remote presumed, kept apart from stated remote
        return "kept_remote_assumed" + tail
    return ("kept_remote_in_location" if lanes.detect_work_mode(where, title) == "remote" else "kept_remote_in_description") + tail


def keep(job):
    return screen(job).startswith("kept_")


class StopOnRateLimit:
    """Wraps a lister: after `limit` rate_limited_429 answers from one ATS family, its remaining boards are not requested this run."""

    def __init__(self, lister_fn, limit=None):
        self.lister_fn, self.limit, self.hits, self.lock = lister_fn, limit or limits.BOARDS_RATE_LIMIT_STOP, {}, threading.Lock()

    def __call__(self, source):
        kind = source["kind"]
        with self.lock:
            if self.hits.get(kind, 0) >= self.limit:
                return lister.Listing(lister.FAILED, reason="skipped_after_rate_limit_429")
        listing = self.lister_fn(source)
        if listing.reason == "rate_limited_429":
            with self.lock:
                self.hits[kind] = self.hits.get(kind, 0) + 1
        return listing


def run(limit, live, now=None, lister_fn=lister.list_source, repo=None, sources=None):
    counts = web.run(limit, live, now, StopOnRateLimit(lister_fn), repo, registry.boards() if sources is None else sources, LANE,
                     cap=limits.BOARDS_PER_RUN, budget=limits.BOARDS_INGEST_PER_RUN, screen=screen, workers=limits.BOARDS_WORKERS)
    counts["pass"] = "employer boards"
    return counts


def acceptance_band(score):
    """The acceptance cuts around the Fit floor of 72: lt60, 60_69, 70_71, 72_up (or unscored)."""
    if score is None:
        return "unscored"
    return next((name for edge, name in ((60, "lt60"), (70, "60_69"), (72, "70_71")) if score < edge), "72_up")


def preview(limit, live, environ=os.environ, lister_fn=lister.list_source, sample=600, seed=99, connection=None):
    """D202 acceptance, read-only, counts only (`live` is ignored): the same random sample of seed boards the review benchmark used (seed 99),
    listed now, screened, and the kept jobs scored by the shared Fit and lane policy with the private profile in memory. The store is only read,
    to tell a job it already holds (and whether that one is published) from a new one. Nothing is written anywhere."""
    from lifeos.jobs import funnel, identity, store                                             # noqa: PLC0415
    from lifeos.jobs.fit import profile as fit_profile, stage as fit_stage                    # noqa: PLC0415
    pick = random.Random(seed).sample(registry.boards(), min(sample, limit or sample))
    with ThreadPoolExecutor(max_workers=limits.BOARDS_WORKERS) as pool:
        listings = list(pool.map(lister_fn, pick))
    jobs = [(s, j) for s, l in zip(pick, listings) if l.status == lister.COMPLETE for j in l.jobs]
    out = {"boards": len(pick), "complete": sum(l.status == lister.COMPLETE for l in listings), "listed": len(jobs), "screen": {}}
    kept = []
    for source, job in jobs:
        why = screen(job)
        out["screen"][why] = out["screen"].get(why, 0) + 1
        if why.startswith("kept_"):
            kept.append((source, job, why))
    kept = list({j["url"]: (s, j, w) for s, j, w in kept}.values())                             # one vacancy per URL
    today = date.today()
    rows = [(i, j["title"], j.get("company") or s["company"], j.get("content") or "", None, LANE, j["location"], None, j["posted"], today, None, "web:" + s["id"])
            for i, (s, j, _) in enumerate(kept)]
    scored = fit_stage.score_rows(rows, fit_profile.load(environ), today)
    keys = [identity.url_hash(j["url"]) for _, j, _ in kept]
    with (connection or store.connect()) as conn, conn.cursor() as cursor:
        cursor.execute("SELECT dedupe_key, notion_page_id IS NOT NULL FROM v7_jobs WHERE dedupe_key IN %s", (tuple(keys) or ("",),))
        held = dict(cursor.fetchall())
    out.update(kept=len(kept), fit_evaluated=len(scored), decision={}, band={}, reason={}, already_held=0, already_published=0, new_admits=0, new_reviews=0,
               fit_band={}, by_evidence={})
    for (_, _, result, decision, _, _, _), key, (_, _, why) in zip(scored, keys, kept):
        out["decision"][decision.status] = out["decision"].get(decision.status, 0) + 1
        band = funnel.band(result.score)
        out["band"][band] = out["band"].get(band, 0) + 1
        code = funnel.reason_code(decision.reason)
        out["reason"][code] = out["reason"].get(code, 0) + 1
        out["already_held"] += key in held
        out["already_published"] += bool(held.get(key))
        out["new_admits"] += decision.status == lanes.ADMIT and key not in held       # would be a new Notion page, under the per-run publish cap
        out["new_reviews"] += decision.status == lanes.REVIEW and key not in held
        cut = acceptance_band(result.score)
        out["fit_band"][cut] = out["fit_band"].get(cut, 0) + 1
        cell = out["by_evidence"].setdefault(why[len("kept_"):], {})                          # remote stated vs assumed, place unresolved
        for name in (decision.status, "fit_" + cut):
            cell[name] = cell.get(name, 0) + 1
    return out

