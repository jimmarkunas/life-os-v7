"""Scale-Up sponsors with no job board of their own: find their jobs through web search (free TinyFish Search), then let the normal pipeline decide.

For each sponsor still on the `fallback` status, one search finds job pages that name the company (aggregators, startup.jobs, UK job boards, the
company's own site). Each result that clearly names the sponsor becomes a NEW candidate (lane Scale-Up, route evidence Scale-up:POSITIVE: membership of the
curated sponsor universe). Candidates then run the same final-link chain as newsletter jobs: employer ATS board match, then one search for the employer's own posting.
Nothing is published without a final employer/ATS link and a full description (D1); an aggregator-only lead is counted unresolved. Counts only in the log."""
from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

from lifeos.jobs import classify, intake, jd, lanes, quality, store
from lifeos.jobs.fit import exclusions as title_rules
from lifeos.jobs.resolve import ats_match, stage
from lifeos.jobs.resolve.aggregators import linkedin
from lifeos.platform import tinyfish_search
from lifeos.platform.tinyfish import TinyFishError
from lifeos.sources.web import registry

SOURCE = "discover"
PER_SPONSOR = 5                  # candidates kept per sponsor per run
RESOLVE_SEARCHES = 40            # final-link searches per run (the shared per-hour search allowance is 500: LinkedIn 100 + this 40 + titlewatch 14; Lensa retired, D201)
_SEPARATORS = re.compile(r"\s+[|\-–—·•]\s+|\s+at\s+|\s+@\s+", re.I)
_SITE_WORD = re.compile(r"\b(jobs?|careers?|vacanc\w+|indeed|linkedin|startup|reed|totaljobs|glassdoor|adzuna|cv-?library|visa|sponsor\w*|apply|hiring|work (?:at|for)|uk|london)\b", re.I)
_NOT_A_ROLE = re.compile(r"^(jobs?|careers?|vacancies|current vacancies|open positions?|work (?:at|for) .*|jobs? (?:in|at) .*)$", re.I)
# LIFE-759: pages that are not a job. Sponsor-directory domains (kept here, one place) never post; a title with no role noun, or one that names a sponsor licence, is not a job title.
SPONSOR_DIRECTORY_HOSTS = ("legitsponsors.co.uk", "sponsordirectory.co.uk", "hirenza.com", "getborderless.co.uk", "atlaspriorityservices.com", "jobvisaai.com", "myvisajobs.co.uk")
_ROLE_NOUN = re.compile(r"\b(manager|engineer|developer|analyst|designer|director|lead|head|officer|executive|specialist|consultant|scientist|architect|coordinator|associate|assistant|administrator|"
                        r"recruiter|marketer|writer|producer|technician|advis[eo]r|strategist|representative|researcher|programmer|accountant|president|vp|cto|cfo|coo|ceo|founder|owner|"
                        r"partner|principal|intern|apprentice|trainee|operator|planner|buyer|controller|auditor|editor|copywriter|translator|lawyer|solicitor|counsel|paralegal|nurse|teacher|"
                        r"tester|sdet|devops|sre|admin|agent|generalist|evangelist|ambassador)s?\b", re.I)
_NOT_A_JOB_TITLE = re.compile(r"sponsor(?:s|ship)?\s+licen[cs]e|licen[cs]ed\s+sponsor|visa\s+sponsor|sponsor\s+(?:directory|list|register)|\bdirectory\b", re.I)
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
        snippet = " ".join((result.get("snippet") or "").split())[:300]
        place = "London, UK" if lanes.geography_status(f"{title} {snippet}") == lanes.POSITIVE else ""      # London, or a UK-wide remote role (D96, D98)
        out.append({"url": url, "title": role, "location": place, "host": classify.host(url), "snippet": snippet})
        if len(out) >= PER_SPONSOR:
            break
    return out


def direct_link(url, company):
    """A search result that is itself a single-job page on the sponsor's own domain, or on an ATS, needs no second search: it IS the employer's posting
    (D3 ranks 1 and 2). Aggregators and recruiters are not: the first token of the sponsor's name must be in the host. Enrich still reads the page and
    refuses a form, a list or a title that does not match."""
    kind = classify.apply_kind(url)
    if quality.link_problem(url):
        return None
    if kind == "ats":
        return {"outcome": "landed", "via": "discover_direct", "kind": "ats", "url": url}
    tokens = _tokens(company)
    compact = re.sub(r"[^a-z0-9]", "", classify.host(url))
    if kind == "employer" and tokens and len(tokens[0]) >= 4 and tokens[0] in compact:
        return {"outcome": "landed", "via": "discover_direct", "kind": "employer", "url": url}
    return None


def requeue_held(cursor):
    """Jobs parked on HOLD by the old two-step resolve (reason `no_match_*`) whose own URL is a direct link now (D77) go back to NEW once. A job that
    fails Enrich afterwards carries a link reason, not `no_match_*`, so it is never requeued again."""
    cursor.execute("SELECT id, source_url, company FROM v7_jobs WHERE source=%s AND status='HOLD' AND unresolved_reason LIKE 'no_match%%'", (SOURCE,))
    ids = [row[0] for row in cursor.fetchall() if direct_link(row[1], row[2] or "")]
    for job_id in ids:
        cursor.execute("UPDATE v7_jobs SET status='NEW', resolve_attempts=0, unresolved_reason='requeued_direct', updated_at=%s WHERE id=%s", (_now(), job_id))
    return len(ids)


def _resolve(rows, budget):
    """Direct links first; whatever is left goes through the ATS board match and the one employer search (shared with LinkedIn and Lensa)."""
    results = [direct_link(row[1], row[2]) or {"outcome": "external_hidden"} for row in rows]
    return linkedin.match_rows(rows, results, budget=budget)


def _admit(cursor, source, job, now):
    key, is_new = intake.add_job(cursor, {"url": job["url"], "status": "NEW", "title": job["title"], "company": source["company"], "location": job["location"],
                                          "salary": None, "source": SOURCE, "provider": "Public Web", "lane": "Scale-Up", "age_days": None, "received": now,
                                          "provider_score": None}, now)
    if is_new:
        cursor.execute("UPDATE v7_jobs SET route_evidence=%s WHERE dedupe_key=%s", ("Scale-up:POSITIVE", key))
    return is_new


LISTING_NOTE = "Found by web search; the listing page was not read."


def is_sponsor_directory(url):
    """True when the listing URL's host is a known sponsor-directory domain or a subdomain of one (by host, never by substring)."""
    host = (urlsplit(url or "").hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in SPONSOR_DIRECTORY_HOSTS)


def is_not_a_job(title, company):
    """True when the search result's title is not a job title: it names a sponsor licence or a directory, is a bare company name, or has no role noun."""
    text = re.sub(r"\s+", " ", title or "").strip()
    if not text or _NOT_A_JOB_TITLE.search(text):
        return True
    if text.casefold() == (company or "").strip().casefold():
        return True
    return not _ROLE_NOUN.search(text)


def promote_leads(cursor, snippets, now=None):
    """LIFE-723 (Jim, Nov 15 2026; relaxes D1 and D36 for this one case): a Scale-Up sponsor with no employer board whose job the chain could not link to the employer (HOLD, no_match_*) is posted
    from its listing page when it passes the Scale-Up rules: a London or UK-wide remote place (D96, D98) and no hard title exclusion (D95). The listing page is never fetched: only the search result's
    own title, snippet and URL are used. The job becomes RESOLVED with link_proof `listing`; Fit and the lane gate then decide it (always Review, never Admit) and Publish posts it with Aggregator Link.
    LIFE-759: a sponsor-directory host or a title that is not a job (see is_sponsor_directory, is_not_a_job) is excluded first, with the fixed reasons sponsor_directory and not_a_job.
    -> counts: promoted, not_london, title_excluded, sponsor_directory, not_a_job."""
    now = now or _now()
    counts = {"promoted": 0, "not_london": 0, "title_excluded": 0, "sponsor_directory": 0, "not_a_job": 0}
    cursor.execute("SELECT id, source_url, title, company, location_text FROM v7_jobs WHERE source=%s AND status='HOLD' AND unresolved_reason LIKE 'no_match%%' AND notion_page_id IS NULL", (SOURCE,))
    for job_id, url, title, company, place in cursor.fetchall():
        reason = "sponsor_directory" if is_sponsor_directory(url) else "not_a_job" if is_not_a_job(title, company) else None
        if reason:
            cursor.execute("UPDATE v7_jobs SET status='EXCLUDED_FIT', unresolved_reason=%s, updated_at=%s WHERE id=%s AND status='HOLD'", (reason, now, job_id))
            counts[reason] += bool(cursor.rowcount)
            continue
        if lanes.geography_status(place) != lanes.POSITIVE:
            counts["not_london"] += 1                                                     # no stated place, or not London: never guessed
            continue
        if title_rules.check(title, company, "")[0]:
            counts["title_excluded"] += 1
            continue
        cursor.execute("UPDATE v7_jobs SET final_apply_url=%s, apply_kind='aggregator', link_proof=%s, status='RESOLVED', unresolved_reason='listing_lead', updated_at=%s"
                       " WHERE id=%s AND status='HOLD'", (url, lanes.LISTING_PROOF, now, job_id))
        if not cursor.rowcount:
            continue
        d = jd.describe(f"{title}\n{(snippets or {}).get(url) or LISTING_NOTE}", is_html=False)
        cursor.execute("INSERT INTO v7_job_descriptions (job_id, source_kind, full_text, summary, responsibilities, requirements, qualifications, fingerprint, fetched_at)"
                       " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE job_id=job_id",
                       (job_id, "listing", d["full_text"], d["summary"], d["responsibilities"], d["requirements"], d["qualifications"], d["fingerprint"], now))
        counts["promoted"] += 1
    return counts


def run(limit, live, search=None, sources=None):
    search = search or tinyfish_search.search
    sponsors = sources if sources is not None else [r for r in registry.load(registry.PATHS["Scale-Up"]) if r["enabled"] and r["status"] == "fallback"]
    counts = {"sponsors": len(sponsors), "searched": 0, "candidates": 0, "added": 0, "search_errors": 0, "requeued": 0, "hosts": {}}
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
            counts["requeued"] = requeue_held(cursor)
    budget = {"left": RESOLVE_SEARCHES}
    counts["resolve"] = stage.run_rows(SOURCE, 60, True, lambda rows: _resolve(rows, budget))
    snippets = {job["url"]: job["snippet"] for _, job in found if job.get("snippet")}
    with store.connect() as connection:
        with connection.cursor() as cursor:
            counts["listing"] = promote_leads(cursor, snippets)                                   # LIFE-723: after the chain, so a real employer link always wins
    return counts
