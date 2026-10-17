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
# Fit already admits these often though a profession word is in them (Ledger, Oct 17: 55 Product Marketing roles Admitted, average Fit 80): Fit decides.
FIT_ADMITS = re.compile(r"\bproduct marketing\b", re.I)
# Adjacent: operations, strategy and transformation leadership, plausible only when no other profession or junior support word is in the title.
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
    junior = re.search(r"\b(coordinator|assistant|intern|specialist|associate)\b", title, re.I)
    if FIT_ADMITS.search(title) and not junior:
        return "adjacent", None
    hit = NOT_ROLE.search(title)
    if ADJACENT.search(title) and not hit:
        return "adjacent", None
    return None, ("other_profession_" + hit.group(1).lower().replace(" ", "_")) if hit else "no_role_term"


US = re.compile(r"united states|\busa?\b|\bu\.s\.", re.I)
REMOTE = re.compile(r"\bremote\b|\banywhere\b", re.I)
PLACELESS = re.compile(r"^\W*(?:fully\W+)?(?:remote|anywhere|worldwide|global)(?:\W+(?:remote|anywhere|worldwide|global))*\W*$", re.I)   # "Remote", "Remote - Anywhere"
WHOLE_US = re.compile(r"^\W*(?:united states(?: of america)?|usa?|u\.s\.(?:a\.)?)\W*$", re.I)                                        # the country, no city


def screen(job):
    """One named reason per listed job; a job is stored only when it starts with "kept_". The title must be a plausible role (`role`). Remote must be SHOWN, by the place or title or by an
    explicit statement in the description (lanes.detect_work_mode); a bare "United States" is a market, not remote evidence, so it is
    `work_mode_unknown`. Geography: a US place, the whole US, or plain remote. Last, the web pass's own cheap suppression (age is no reason)."""
    title, where, text = job["title"], job["location"] or "", job.get("content") or ""
    tier, why = role(title)
    if not tier:
        return "title_" + why
    if not (US.search(where) or suppress.US.search(where) or WHOLE_US.match(where) or PLACELESS.match(where)):
        return "non_us"
    mode = lanes.detect_work_mode(where, title, text)
    if mode != "remote":
        return "work_mode_unknown" if mode == "unknown" else "not_remote_" + mode
    why = suppress.reason(job, None, LANE, first_party=True)
    if why:
        return "suppressed_" + why
    return "kept_remote_in_location" if lanes.detect_work_mode(where, title) == "remote" else "kept_remote_in_description"


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
            kept.append((source, job))
    kept = list({j["url"]: (s, j) for s, j in kept}.values())                                   # one vacancy per URL
    today = date.today()
    rows = [(i, j["title"], j.get("company") or s["company"], j.get("content") or "", None, LANE, j["location"], None, j["posted"], today, None, "web:" + s["id"])
            for i, (s, j) in enumerate(kept)]
    scored = fit_stage.score_rows(rows, fit_profile.load(environ), today)
    keys = [identity.url_hash(j["url"]) for _, j in kept]
    with (connection or store.connect()) as conn, conn.cursor() as cursor:
        cursor.execute("SELECT dedupe_key, notion_page_id IS NOT NULL FROM v7_jobs WHERE dedupe_key IN %s", (tuple(keys) or ("",),))
        held = dict(cursor.fetchall())
    out.update(kept=len(kept), fit_evaluated=len(scored), decision={}, band={}, reason={}, already_held=0, already_published=0, new_admits=0, new_reviews=0)
    for (_, _, result, decision, _, _, _), key in zip(scored, keys):
        out["decision"][decision.status] = out["decision"].get(decision.status, 0) + 1
        band = funnel.band(result.score)
        out["band"][band] = out["band"].get(band, 0) + 1
        code = funnel.reason_code(decision.reason)
        out["reason"][code] = out["reason"].get(code, 0) + 1
        out["already_held"] += key in held
        out["already_published"] += bool(held.get(key))
        out["new_admits"] += decision.status == lanes.ADMIT and key not in held       # would be a new Notion page, under the per-run publish cap
        out["new_reviews"] += decision.status == lanes.REVIEW and key not in held
    return out

