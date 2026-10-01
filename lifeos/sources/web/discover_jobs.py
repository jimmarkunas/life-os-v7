"""Scale-Up sponsors with no job board of their own: find their jobs through web search (free TinyFish Search), then let the normal pipeline decide.

For each sponsor still on the `fallback` status, one search finds job pages that name the company (aggregators, startup.jobs, UK job boards, the
company's own site). Each result that clearly names the sponsor becomes a NEW candidate (lane Scale-Up, route evidence Scale-up:POSITIVE: membership of the
curated sponsor universe). Candidates then run the same final-link chain as newsletter jobs: employer ATS board match, then one search for the employer's own posting.
Nothing is published without a final employer/ATS link and a full description (D1); an aggregator-only lead is counted unresolved. Counts only in the log."""
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

from lifeos.jobs import classify, intake, quality, store
from lifeos.jobs.resolve import ats_match, stage
from lifeos.jobs.resolve.aggregators import linkedin
from lifeos.platform import tinyfish_search
from lifeos.platform.tinyfish import TinyFishError
from lifeos.sources.web import registry

SOURCE = "discover"
PER_SPONSOR = 5                  # candidates kept per sponsor per run
RESOLVE_SEARCHES = 40            # final-link searches per run (the shared per-hour search allowance is 500; Lensa 300 + LinkedIn 100)
_SEPARATORS = re.compile(r"\s+[|\-–—·•]\s+|\s+at\s+|\s+@\s+", re.I)
_SITE_WORD = re.compile(r"\b(jobs?|careers?|vacanc\w+|indeed|linkedin|startup|reed|totaljobs|glassdoor|adzuna|cv-?library|visa|sponsor\w*|apply|hiring|work (?:at|for)|uk|london)\b", re.I)
_NOT_A_ROLE = re.compile(r"^(jobs?|careers?|vacancies|current vacancies|open positions?|work (?:at|for) .*|jobs? (?:in|at) .*)$", re.I)
_PROFILE_PATH = re.compile(r"/(company|companies|cmp|employers?|organisations?|profile)/[^/]+/?$", re.I)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _tokens(company):
    return [w for w in ats_match.norm(company).split() if w not in ats_match.SUFFIX and len(w) > 2][:2]


def role_from_title(page_title, company):
    """'Product Manager at Sixfold Bioscience | startup.jobs' -> 'Product Manager'; '' when no plausible role is left."""
    tokens = _tokens(company)
    for part in _SEPARATORS.split(page_title or ""):
        part = " ".join(part.split()).strip(" -|")
        if not 4 <= len(part) <= 120 or _NOT_A_ROLE.match(part):
            continue
        folded = ats_match.norm(part)
        if tokens and all(t in folded for t in tokens):
            continue                                       # the company's name, not the role
        if _SITE_WORD.fullmatch(part) or re.fullmatch(r"[\w.-]+\.(?:com|co\.uk|io|org|net)", part, re.I):
            continue                                       # a site name
        return part
    return ""


def candidates(company, results):
    """Job candidates from search results: a single-job-looking URL, the sponsor named in the page title/snippet/url, and a role we can read."""
    tokens = _tokens(company)
    out, seen = [], set()
    for result in results[:8]:
        url = result.get("url") or ""
        title = result.get("title") or ""
        hay = ats_match.norm(url + " " + title + " " + (result.get("snippet") or ""))
        if not tokens or not all(t in hay for t in tokens):
            continue
        if quality.url_problem(url) or _PROFILE_PATH.search(urlsplit(url).path):
            continue
        role = role_from_title(title, company)
        if not role or url in seen:
            continue
        seen.add(url)
        place = "London, UK" if "london" in ats_match.norm(title + " " + (result.get("snippet") or "")) else ""
        out.append({"url": url, "title": role, "location": place, "host": classify.host(url)})
        if len(out) >= PER_SPONSOR:
            break
    return out


def _admit(cursor, source, job, now):
    key, is_new = intake.add_job(cursor, {"url": job["url"], "status": "NEW", "title": job["title"], "company": source["company"], "location": job["location"],
                                          "salary": None, "source": SOURCE, "provider": "Public Web", "lane": "Scale-Up", "age_days": None, "received": now,
                                          "provider_score": None}, now)
    if is_new:
        cursor.execute("UPDATE v7_jobs SET route_evidence=%s WHERE dedupe_key=%s", ("Scale-up:POSITIVE", key))
    return is_new


def run(limit, live, search=None, sources=None):
    search = search or tinyfish_search.search
    sponsors = sources if sources is not None else [r for r in registry.load(registry.PATHS["Scale-Up"]) if r["enabled"] and r["status"] == "fallback"]
    counts = {"sponsors": len(sponsors), "searched": 0, "candidates": 0, "added": 0, "search_errors": 0, "hosts": {}}
    found = []
    for source in sponsors[:limit]:
        try:
            results = search(f"{source['company']} jobs vacancies UK")
        except TinyFishError:
            counts["search_errors"] += 1
            continue
        counts["searched"] += 1
        for job in candidates(source["company"], results):
            counts["candidates"] += 1
            counts["hosts"][job["host"]] = counts["hosts"].get(job["host"], 0) + 1
            found.append((source, job))
    if not live:
        counts["dry_run"] = True
        return counts
    now = _now()
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            for source, job in found:
                counts["added"] += _admit(cursor, source, job, now)
    budget = {"left": RESOLVE_SEARCHES}
    counts["resolve"] = stage.run_rows(SOURCE, 60, True,
                                       lambda rows: linkedin.match_rows(rows, [{"outcome": "external_hidden"} for _ in rows], budget=budget))
    return counts
