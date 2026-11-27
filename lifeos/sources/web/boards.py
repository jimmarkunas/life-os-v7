"""The employer-board pass: Greenhouse, Ashby and Lever boards read through the shared web run.

Same states, diff and admission as the priority boards, with tighter bounds: more boards per run on fewer workers,
slower refresh unless the board lists a target job, a smaller admission budget, only targets stored. An ATS that
keeps answering 429 is skipped for the rest of the run (`skipped_after_rate_limit_429`)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from lifeos.jobs import lanes
from lifeos.platform import boards_window, ct_stamp, switches
from lifeos.platform import limits
from lifeos.sources.web import lister, registry, run as web, suppress

LANE = "US Remote"
# Role screen: pass every plausible role to the shared Fit and name every title it rejects.
# Strong: program, project, product, delivery, implementation, transformation or solutions leadership.
STRONG = re.compile(
    r"\b(program(?:me)? (?:manag\w*|lead|director|head)|(?:technical|engineering|product|ai|data|security|it|infrastructure|platform) program(?:me)?s?\b|"
    r"tpm|project (?:manag\w*|lead|director)|delivery (?:manag\w*|lead|director|head)|head of (?:delivery|programs?|programmes?|product|pmo|transformation)|"
    r"implementation (?:manag\w*|lead|director|consultant)|product (?:manag\w*|lead|owner|director|head)|(?:director|vp|vice president|head) of product(?! marketing| design)|"
    r"chief of staff|transformation (?:lead|manag\w*|director|head|program)|pmo|engagement manager|solutions? (?:architect|consultant)|portfolio (?:manag\w*|lead|director)|"
    r"release manag\w*)\b",
    re.I,
)
# Adjacent: operations, strategy and transformation leadership, only when no profession or support word appears.
# Product Marketing is not passed: Fit cannot tell it from the target family.
ADJACENT = re.compile(
    r"\b((?:business|strategy (?:and|&)|strategic|technical) operations|(?:director|vp|vice president|head|chief) of (?:operations|strategy)|"
    r"operations (?:director|lead|manager|head)|strategy (?:and|&) operations|transformation|(?:director|head|vp|vice president)\b[^,]{0,25}\bstrategy|"
    r"head of (?:technology|engineering) (?:&|and) product)\b",
    re.I,
)
NOT_ROLE = re.compile(
    r"\b(coordinator|assistant|specialist|representative|designer|engineer|developer|scientist|marketing|sales|account executive|recruit\w*|"
    r"accountant|accounting|payroll|nurse|clinical|technician|driver|analyst|associate|clerk|customer (?:success|support|service)|finance|financial|"
    r"legal|counsel|people|hr|talent|facilities|warehouse|construction|revenue|franchise|underwriting|officer)\b",
    re.I,
)
NOT_A_ROLE = re.compile(
    r"\b(intern|internship|coach|mentor|tutor|instructor|teacher|future (?:tech )?positions?)\b", re.I
)


def role(title):
    out = NOT_A_ROLE.search(title)
    if out:
        return None, "not_a_role_" + out.group(1).lower().split()[0]
    if STRONG.search(title):
        return "strong", None
    hit = NOT_ROLE.search(title)
    if ADJACENT.search(title) and not hit:
        return "adjacent", None
    return None, ("other_profession_" + hit.group(1).lower().replace(" ", "_")) if hit else "no_role_term"


US = re.compile(r"united states|\busa?\b|\bu\.s\.", re.I)
REMOTE = re.compile(r"\bremote\b|\banywhere\b", re.I)
WHOLE_US = re.compile(r"^\W*(?:united states(?: of america)?|usa?|u\.s\.(?:a\.)?)\W*$", re.I)


# non-US only when it names somewhere outside the US and nothing in it is US; otherwise unresolved
NON_US_NAMED = re.compile(
    r"\b(algeria|algiers|egypt|cairo|nigeria|lagos|kenya|nairobi|south africa|morocco|tunisia|saudi|riyadh|dubai|uae|qatar|turkey|istanbul|"
    r"pakistan|karachi|lahore|bangladesh|dhaka|sri lanka|china|shanghai|beijing|shenzhen|hong kong|taiwan|taipei|korea|seoul|vietnam|hanoi|"
    r"ho chi minh|thailand|bangkok|indonesia|jakarta|malaysia|kuala lumpur|colombia|bogot[a\u00e1]|argentina|buenos aires|brazil|s[a\u00e3]o paulo|"
    r"chile|santiago|peru|lima|costa rica|ecuador|nicaragua|uruguay|guatemala|mexico|latam|latin america|emea|apac|europe|asia|africa|middle east|"
    r"greece|athens|piraeus|lithuania|vilnius|latvia|riga|estonia|tallinn|romania|bucharest|bulgaria|sofia|serbia|belgrade|croatia|zagreb|"
    r"hungary|budapest|czech|prague|slovakia|slovenia|austria|vienna|switzerland|zurich|geneva|belgium|brussels|luxembourg|frankfurt|"
    r"düsseldorf|dusseldorf|hamburg|cologne|denmark|copenhagen|sweden|stockholm|norway|oslo|finland|helsinki|italy|milan|rome|"
    r"noida|gurgaon|gurugram|chennai|delhi|kolkata|phl|nepal|new zealand|auckland|espoo|abu dhabi|united arab emirates|calgary|edmonton|"
    r"guadalajara|wroc[lł]aw|krak[oó]w|kyiv|kiev|taoyuan|cyprus|baku|canberra|lilongwe|malawi|maputo|mozambique|kinshasa|mauritius|"
    r"tel[- ]aviv)\b",
    re.I,
)


INCLUDES_US = re.compile(
    r"\b(north america|namer|americas|global|worldwide|anywhere)\b", re.I
)  # may include the US: unresolved, never non-US


def geography(where):
    where = where or ""
    us = US.search(where) or suppress.US.search(where) or WHOLE_US.match(where) or lanes.US_PLACES.search(where)
    if not us and INCLUDES_US.search(where):
        return "unresolved"
    if not us and (
        lanes.market_of(where) in ("UK", "OTHER") or suppress.NON_US.search(where) or NON_US_NAMED.search(where)
    ):
        return "non_us"
    return "us" if us else "unresolved"


def screen(job):
    """One named reason per listed job; only "kept_" reasons are stored. A US job is presumed remote unless the
    employer requires office attendance; an unnamed place is `_place_unresolved`."""
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
        return "not_remote_" + mode
    why = suppress.reason(job, None, LANE, first_party=True)
    if why:
        return "suppressed_" + why
    if mode == "unknown":
        return "kept_remote_assumed" + tail
    return (
        "kept_remote_in_location" if lanes.detect_work_mode(where, title) == "remote" else "kept_remote_in_description"
    ) + tail


class StopOnRateLimit:
    def __init__(self, lister_fn, limit=None):
        self.lister_fn, self.limit, self.hits, self.lock = (
            lister_fn,
            limit or limits.BOARDS_RATE_LIMIT_STOP,
            {},
            threading.Lock(),
        )

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


def run(
    limit, live, now=None, lister_fn=lister.list_source, repo=None, sources=None, environ=None, clock=time.monotonic
):
    """Re-read boards already read once (plus curated extras and a forced board), oldest-due first, at most
    BOARDS_PER_RUN (BOARDS_CATCHUP_PER_RUN under BOARDS_MODE=catchup). BOARDS_DEADLINE_MINUTES stops new reads
    early so the receipt is written."""
    environ = os.environ if environ is None else environ
    started = now or web._now()
    began = clock()
    catchup = environ.get("BOARDS_MODE") == "catchup"
    cap = limits.BOARDS_CATCHUP_PER_RUN if catchup else limits.BOARDS_PER_RUN
    try:
        minutes = float(environ.get("BOARDS_DEADLINE_MINUTES") or 0)
    except ValueError:
        minutes = 0
    end = began + minutes * 60
    counts = web.run(
        limit,
        live,
        started,
        StopOnRateLimit(lister_fn),
        repo,
        registry.boards() if sources is None else sources,
        LANE,
        cap=cap,
        budget=limits.BOARDS_INGEST_PER_RUN,
        screen=screen,
        workers=limits.BOARDS_WORKERS,
        select="known",
        stop=(lambda: clock() >= end) if minutes > 0 else None,
    )
    counts["pass"] = "employer boards"
    counts["receipt"] = receipt(
        counts, environ, started, started + timedelta(seconds=max(clock() - began, 0)), cap, catchup
    )
    path = environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a") as handle:
                handle.write(receipt_markdown(counts["receipt"]))
        except OSError:
            pass
    return counts


def tick(limit, live, now=None, environ=os.environ, running=boards_window.window_running, **kw):
    """The due-only board pass inside the Jobs tick; runs only while BOARDS_IN_TICK makes the tick the owner and no
    board window run is active, else it reads nothing and opens no store session."""
    out = {"pass": "employer boards (tick)", "owner": boards_window.owner(environ)}
    if out["owner"] != "tick":
        return {**out, "skipped": True, "why": "windows_own"}
    if running():
        return {**out, "skipped": True, "why": "window_running"}
    counts = run(
        min(limit, limits.BOARDS_PER_RUN),
        live,
        now,
        environ={
            **environ,
            "BOARDS_DEADLINE_MINUTES": str(limits.BOARDS_TICK_DEADLINE_MINUTES),
            "BOARDS_MODE": "window",
        },
        **kw,
    )
    return {**counts, "owner": "tick", "pass": "employer boards (tick)"}


def receipt_markdown(r):
    rows = [
        ("Window", f"{r['window'] or 'manual'} ({r['mode']}, cap {r['cap']})"),
        ("Started / finished", f"{r['started']} / {r['finished']}"),
        ("Due at start", r["due"]),
        ("Processed", r["processed"]),
        (
            "Deferred (carried forward)",
            f"{r['carry_forward']['boards']} to the {r['carry_forward']['next_window']} window",
        ),
        ("Failed", r["failed"]),
        ("Backlog age at start (hours)", r["backlog_age_hours"]),
        ("Oldest carried board (hours overdue)", r["oldest_deferred_hours"]),
        ("Freshness ceiling for a hot board (hours)", r["freshness_ceiling_hours"]),
        ("Provider-limit answers (429)", r["provider_limit_hits"]["total"]),
        ("Late-window reason", r["late_reason"] or "none"),
        ("Stopped at the soft deadline", "yes" if r.get("stopped_at_deadline") else "no"),
    ]
    rows += [
        ("Descriptions rewritten (Fit re-scores each next)", r["jd_reread"]),
        ("Boards skipped with zero per-posting work", r["cache_skips"]),
    ]
    if r.get("timing"):
        t = r["timing"]
        rows += [
            (
                "Pass wall / waiting for reads / saving (s)",
                f"{t['wall_s']} / {t['waiting_for_reads_s']} / {t['saving_s']}",
            ),
            ("Read s per board (p50, p95)", t["read_s_p50_p95"]),
            ("Save s per board (p50, p95)", t["save_s_p50_p95"]),
        ]
    return (
        "### Employer boards: window receipt (LIFE-854)\n\n| Field | Value |\n|---|---|\n"
        + "\n".join(f"| {k} | {v} |" for k, v in rows)
        + "\n"
    )


def receipt(counts, environ, started, finished, cap, catchup):
    failed_why = counts.get("failed_why") or {}
    hits = {
        "rate_limited_429": failed_why.get("rate_limited_429", 0),
        "skipped_after_rate_limit_429": failed_why.get("skipped_after_rate_limit_429", 0),
    }
    aware = started.replace(tzinfo=timezone.utc)
    try:
        slot = datetime.strptime(environ["BOARDS_SLOT"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        slot = boards_window.current_slot(aware)
    deferred, oldest = counts.get("deferred", 0), counts.get("oldest_deferred_hours", 0.0)
    out = {
        "window": environ.get("BOARDS_SLOT_CT", ""),
        "mode": "catchup" if catchup else "window",
        "cap": cap,
        "late_reason": environ.get("BOARDS_LATE_REASON", ""),
        "due": counts.get("due_total", 0),
        "processed": counts["complete"] + counts["failed"],
        "deferred": deferred,
        "failed": counts["failed"],
        "backlog_age_hours": counts.get("oldest_due_hours", 0.0),
        "oldest_deferred_hours": oldest,
        "freshness_ceiling_hours": limits.BOARDS_HOT_HOURS,
        "carry_within_ceiling": oldest <= limits.BOARDS_HOT_HOURS,
        "provider_limit_hits": {**hits, "total": sum(hits.values())},
        "started": ct_stamp.stamp(aware),
        "finished": ct_stamp.stamp(finished.replace(tzinfo=timezone.utc)),
        "carry_forward": {
            "boards": deferred,
            "oldest_overdue_hours": oldest,
            "next_window": boards_window.label(boards_window.next_slot(slot)),
        },
    }
    if counts.get("stopped_at_deadline"):
        out["stopped_at_deadline"] = True
    out["jd_reread"], out["fit_rescore_due"], out["cache_skips"] = (
        counts.get("jd_reread", 0),
        counts.get("fit_rescore_due", 0),
        counts.get("cache_skips", 0),
    )
    if counts.get("timing"):
        out["timing"] = counts["timing"]
    return out


def backfill(limit, live, now=None, lister_fn=lister.list_source, repo=None, sources=None, environ=os.environ):
    """Read never-read boards, at most BOARDS_BACKFILL_PER_RUN in id order; off unless BOARDS_BACKFILL=on. "No
    v7_sources row yet" is the resume cursor; when none is left it reports `drained`."""
    if not switches.state("BOARDS_BACKFILL", environ):
        return {"pass": "employer boards backfill", "backfill": "off"}
    counts = web.run(
        min(limit, limits.BOARDS_BACKFILL_PER_RUN),
        live,
        now,
        StopOnRateLimit(lister_fn),
        repo,
        registry.boards() if sources is None else sources,
        LANE,
        cap=limits.BOARDS_BACKFILL_PER_RUN,
        budget=limits.BOARDS_INGEST_PER_RUN,
        screen=screen,
        workers=limits.BOARDS_WORKERS,
        select="unread",
    )
    counts["pass"] = "employer boards backfill"
    counts["read"], counts["errors"] = counts["due"], counts["failed"]
    counts["remaining"] = counts["unread"] - (counts["due"] if live else 0)
    counts["backfill"] = "drained" if counts["remaining"] == 0 else "on"
    return counts


def acceptance_band(score):
    if score is None:
        return "unscored"
    return next((name for edge, name in ((60, "lt60"), (70, "60_69"), (72, "70_71")) if score < edge), "72_up")


def preview(limit, live, environ=os.environ, lister_fn=lister.list_source, sample=600, seed=99, connection=None):
    """Read-only preview, counts only (`live` ignored): screen a seeded sample of boards and score the kept jobs."""
    from lifeos.jobs import funnel, identity, store  # noqa: PLC0415
    from lifeos.jobs.fit import profile as fit_profile, stage as fit_stage  # noqa: PLC0415

    pick = random.Random(seed).sample(registry.boards(), min(sample, limit or sample))
    with ThreadPoolExecutor(max_workers=limits.BOARDS_WORKERS) as pool:
        listings = list(pool.map(lister_fn, pick))
    jobs = [(s, j) for s, l in zip(pick, listings) if l.status == lister.COMPLETE for j in l.jobs]
    out = {
        "boards": len(pick),
        "complete": sum(l.status == lister.COMPLETE for l in listings),
        "listed": len(jobs),
        "screen": {},
    }
    kept = []
    for source, job in jobs:
        why = screen(job)
        out["screen"][why] = out["screen"].get(why, 0) + 1
        if why.startswith("kept_"):
            kept.append((source, job, why))
    kept = list({j["url"]: (s, j, w) for s, j, w in kept}.values())
    today = date.today()
    rows = [
        (
            i,
            j["title"],
            j.get("company") or s["company"],
            j.get("content") or "",
            None,
            LANE,
            j["location"],
            None,
            j["posted"],
            today,
            None,
            "web:" + s["id"],
        )
        for i, (s, j, _) in enumerate(kept)
    ]
    scored = fit_stage.score_rows(rows, fit_profile.load(environ), today)
    keys = [identity.url_hash(j["url"]) for _, j, _ in kept]
    with connection or store.connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT dedupe_key, notion_page_id IS NOT NULL FROM v7_jobs WHERE dedupe_key IN %s", (tuple(keys) or ("",),)
        )
        held = dict(cursor.fetchall())
    out.update(
        kept=len(kept),
        fit_evaluated=len(scored),
        decision={},
        band={},
        reason={},
        already_held=0,
        already_published=0,
        new_admits=0,
        new_reviews=0,
        fit_band={},
        by_evidence={},
    )
    for (_, _, result, decision, _, _, _), key, (_, _, why) in zip(scored, keys, kept):
        out["decision"][decision.status] = out["decision"].get(decision.status, 0) + 1
        band = funnel.band(result.score)
        out["band"][band] = out["band"].get(band, 0) + 1
        code = lanes.public_reason(decision.reason)
        out["reason"][code] = out["reason"].get(code, 0) + 1
        out["already_held"] += key in held
        out["already_published"] += bool(held.get(key))
        out["new_admits"] += decision.status == lanes.ADMIT and key not in held
        out["new_reviews"] += decision.status == lanes.REVIEW and key not in held
        cut = acceptance_band(result.score)
        out["fit_band"][cut] = out["fit_band"].get(cut, 0) + 1
        cell = out["by_evidence"].setdefault(why[len("kept_") :], {})
        for name in (decision.status, "fit_" + cut):
            cell[name] = cell.get(name, 0) + 1
    return out
