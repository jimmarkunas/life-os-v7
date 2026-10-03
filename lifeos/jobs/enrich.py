"""Enrich stage: RESOLVED rows -> read the FINAL page for description, real posting date, liveness, and a match check.

Source order for the description: ATS API (Greenhouse, Lever) -> schema.org JobPosting JSON-LD -> readable page text.
Outcomes: READY (fresh, live, matches) | CLOSED | EXCLUDED_STALE (older than the job's LANE policy allows, known; lifeos.jobs.lanes owns the number) | back to NEW (page does not match the
job: a wrong link is never published) | pending with a fixed reason when the page cannot be read (retried later).
Plain HTTP only. Logs: counts only.
"""
from datetime import date, datetime, timedelta, timezone
import json
import re
from urllib.parse import urlsplit

from lifeos.jobs.resolve import ats_match, search_match
from lifeos.platform import usage, limits, tinyfish
from lifeos.jobs import ats_detail, jd, jsonld, lanes, quality, store
from lifeos.platform.http import fetch

TRIED_REASON = "description_empty_tf"     # the rendered fetch already ran once and the page was still empty: do not spend the cap again
CLOSED_TEXT = re.compile(r"no longer accepting|no longer available|position (has been|is) filled|job (is )?closed|"
                         r"posting (has )?expired|this job has expired|not accepting applications", re.I)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _api_job(url):
    """Greenhouse / Lever public job APIs -> dict(title, html, posted) or None."""
    parts = urlsplit(url)
    host, segs = (parts.hostname or "").lower(), [s for s in parts.path.split("/") if s]
    if host.endswith("greenhouse.io"):
        board = (re.search(r"[?&]for=([^&]+)", parts.query) or [None, segs[0] if segs else ""])[1]
        ident = (re.search(r"[?&]token=(\d+)", parts.query) or [None, None])[1] or next(
            (s for s in reversed(segs) if s.isdigit()), None)
        if board and ident:
            page = fetch(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{ident}", timeout=limits.ATS_TIMEOUT_SECONDS,
                         max_hops=2)
            if page.status in (404, 410):
                return {"closed": True}                        # the board's API no longer knows the job
            if page.status == 200:
                try:
                    data = json.loads(page.html)
                except ValueError:
                    return None
                posted = str(data.get("first_published") or data.get("updated_at") or "")[:10]
                return {"title": data.get("title"), "html": data.get("content") or "", "posted": posted}
    if host == "jobs.lever.co" and len(segs) >= 2:
        page = fetch(f"https://api.lever.co/v0/postings/{segs[0]}/{segs[1]}", timeout=limits.ATS_TIMEOUT_SECONDS, max_hops=2)
        if page.status in (404, 410):
            return {"closed": True}
        if page.status == 200:
            try:
                data = json.loads(page.html)
            except ValueError:
                return None
            created = data.get("createdAt")
            posted = datetime.fromtimestamp(created / 1000, timezone.utc).date().isoformat() if created else ""
            return {"title": data.get("text"), "html": data.get("description") or "", "plain": data.get("descriptionPlain"),
                    "posted": posted}
    return None


def _parse_date(value):
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def _title_ok(want, *found):
    """Lenient: most of the job-title words appear in the title / page text found at the final link."""
    tokens = [t for t in ats_match.norm(want).split() if len(t) > 2]
    hay = ats_match.norm(" ".join(f for f in found if f))
    return bool(tokens) and sum(t in hay.split() for t in tokens) / len(tokens) >= 0.6


def read_page(url, title, lane=None):
    """Facts for one final URL. Never raises; fixed outcome codes."""
    problem = quality.link_problem(url)
    if problem:
        return {"outcome": "mismatch", "reason": problem}                    # workable_no_account: re-resolve, the matcher now keeps the account
    api = ats_detail.read(url) or _api_job(url)
    if api and api.get("closed"):
        return {"outcome": "closed"}
    if api:
        text_source = api.get("plain") or api["html"]
        desc = jd.describe(text_source, is_html=not api.get("plain"))
        found_title, posted, source_kind = api.get("title") or desc["full_text"][:300], _parse_date(api.get("posted")), "ats_api"
        valid_through = None
    else:
        page = fetch(url, timeout=15, max_hops=6)
        if page.status in (404, 410):
            return {"outcome": "closed"}
        if page.status != 200 or not page.html:
            return {"outcome": "blocked", "reason": f"http_{page.status or 0}"}
        result = parse_html(url, title, page.html, lane)
        if (urlsplit(url).hostname or "").lower().endswith("dice.com") and "easy apply" in page.html.lower():
            result["apply_kind"] = "easy_apply"            # a Dice job page (JSON-LD description, probe run 85) that is applied to on Dice itself: flag it
        return result
    return finish(title, desc, found_title, posted, source_kind, valid_through, lane)


def parse_html(url, title, html, lane=None):
    """Description / date / liveness / title-match from already-fetched page HTML (plain HTTP or TinyFish Fetch)."""
    posting = jsonld.job_posting(html)
    body_text = jd.html_to_text(html)
    if CLOSED_TEXT.search(body_text[:4000]):
        return {"outcome": "closed"}
    if posting and posting.get("description"):
        desc, source_kind = jd.describe(str(posting["description"]), is_html=True), "jsonld"
        found_title, posted = posting.get("title") or "", jsonld.posted_date(posting)
        valid_through = _parse_date(str(posting.get("validThrough") or ""))
    else:
        embedded = jsonld.embedded_description(html)
        if embedded:
            desc, source_kind = jd.describe(embedded, is_html=True), "next_data"
        else:
            desc, source_kind = jd.describe(body_text, is_html=False), "page_text"
        posted = valid_through = None
        found_title = (re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I) or [None, ""])[1]
    return finish(title, desc, found_title, posted, source_kind, valid_through, lane)


def finish(title, desc, found_title, posted, source_kind, valid_through, lane=None):
    if len(desc["full_text"]) < 200:
        return {"outcome": "blocked", "reason": "description_empty", "source_kind": source_kind}
    problem = quality.jd_problem(desc["full_text"])
    if problem in ("template", "listing"):
        return {"outcome": "mismatch", "reason": "jd_" + problem}       # a form or a list, not this job: wrong link
    if problem == "thin":
        return {"outcome": "blocked", "reason": "jd_thin", "source_kind": source_kind}
    if not _title_ok(title, found_title, desc["full_text"][:1500] if source_kind in ("page_text", "next_data") else ""):
        return {"outcome": "mismatch"}
    today = _now().date()
    if valid_through and valid_through < today:
        return {"outcome": "closed"}
    max_age = lanes.POLICIES[lanes.lane_for(lane)].max_age_days            # the lane policy is the only freshness authority
    if posted and max_age is not None and (today - posted) > timedelta(days=max_age):
        return {"outcome": "stale", "posted": posted, "description": desc, "source_kind": source_kind}
    return {"outcome": "ready", "posted": posted, "description": desc, "source_kind": source_kind}


def save(connection, job_id, result):
    now, outcome = _now(), result["outcome"]
    with connection.cursor() as cursor:
        if outcome in ("ready", "stale"):
            d = result["description"]
            cursor.execute(
                "INSERT INTO v7_job_descriptions (job_id, source_kind, full_text, summary, responsibilities, requirements,"
                " qualifications, fingerprint, fetched_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE"
                " source_kind=VALUES(source_kind), full_text=VALUES(full_text), summary=VALUES(summary),"
                " responsibilities=VALUES(responsibilities), requirements=VALUES(requirements),"
                " qualifications=VALUES(qualifications), fingerprint=VALUES(fingerprint), fetched_at=VALUES(fetched_at)",
                (job_id, result["source_kind"], d["full_text"], d["summary"], d["responsibilities"], d["requirements"],
                 d["qualifications"], d["fingerprint"], now))
            if result.get("apply_kind"):
                cursor.execute("UPDATE v7_jobs SET apply_kind=%s WHERE id=%s", (result["apply_kind"], job_id))
            posted = result.get("posted")
            age = (now.date() - posted).days if posted else None
            cursor.execute("UPDATE v7_jobs SET status=%s, posted_date=%s, posted_source=%s, final_apply_url=%s, "
                           "posted_age_days=COALESCE(%s, posted_age_days), unresolved_reason=NULL, updated_at=%s WHERE id=%s",
                           ("READY" if outcome == "ready" else "EXCLUDED_STALE", posted,
                            "employer" if posted else None, result.get("final_url") or None, age, now, job_id))
        elif outcome == "closed":
            cursor.execute("UPDATE v7_jobs SET status='CLOSED', unresolved_reason='closed', updated_at=%s WHERE id=%s",
                           (now, job_id))
        elif outcome == "mismatch":                      # wrong link: take it back, never publish it
            cursor.execute("UPDATE v7_jobs SET status=IF(resolve_attempts+1>=%s, 'HOLD', 'NEW'), final_apply_url=NULL, apply_kind=NULL, "
                           "unresolved_reason=%s, resolve_attempts=resolve_attempts+1, updated_at=%s WHERE id=%s",   # an attempt: a link that keeps failing parks on HOLD, never loops
                           (limits.RESOLVE_MAX_ATTEMPTS, (result.get("reason") or "link_mismatch")[:100], now, job_id))
        else:
            cursor.execute("UPDATE v7_jobs SET unresolved_reason=%s, enrich_attempts=enrich_attempts+1, "
                           "status=IF(enrich_attempts>=%s,'HOLD',status), updated_at=%s WHERE id=%s",
                           ((result.get("reason") or "blocked")[:100], limits.ENRICH_MAX_ATTEMPTS, now, job_id))


def host_family(url):
    """Counts-only label for where a page lives: a known ATS domain, LinkedIn, or 'employer_site' (never a company name)."""
    host = (urlsplit(url or "").hostname or "").lower()
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return "linkedin"
    for domain in search_match.ATS_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return domain
    return "employer_site"


def blocked_report(results, urls):
    """Why pages stayed blocked, as counts: {'reasons': {code: n}, 'families': {host family: n}}."""
    reasons, families, sources = {}, {}, {}
    for job_id, result in results:
        if result["outcome"] == "blocked":
            kind = result.get("source_kind") or "none"
            sources[kind] = sources.get(kind, 0) + 1
            reasons[result.get("reason") or "blocked"] = reasons.get(result.get("reason") or "blocked", 0) + 1
            family = host_family(urls.get(job_id))
            families[family] = families.get(family, 0) + 1
    return {"reasons": reasons, "families": families, "by_source": sources}


def mismatch_report(results, urls, source_of, previous):
    """Counts only: why links are rejected, which producer and site family they came from, and how many were already rejected last time
    (a repeat means the same wrong link keeps coming back). Never a title, company or URL."""
    reasons, sources, families, repeat = {}, {}, {}, 0
    for job_id, result in results:
        if result["outcome"] != "mismatch":
            continue
        reason = result.get("reason") or "title"
        reasons[reason] = reasons.get(reason, 0) + 1
        sources[source_of.get(job_id) or "unknown"] = sources.get(source_of.get(job_id) or "unknown", 0) + 1
        family = host_family(urls.get(job_id))
        families[family] = families.get(family, 0) + 1
        repeat += previous.get(job_id) == (result.get("reason") or "link_mismatch")
    return {"reasons": reasons, "by_source": sources, "families": families, "repeat": repeat}


def run(limit, live):
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, final_apply_url, title, apply_kind, unresolved_reason, lane, source FROM v7_jobs WHERE status='RESOLVED' "
                           "AND final_apply_url IS NOT NULL ORDER BY last_seen DESC LIMIT %s", (limit,))
            fetched = [tuple(r) for r in cursor.fetchall()]
    rows = [r[:5] for r in fetched]
    lane_of = {r[0]: r[5] for r in fetched}
    source_of = {r[0]: r[6] for r in fetched}
    counts = {"picked": len(rows), "outcome": {}, "source": {}}
    results = []
    for job_id, url, title, kind, _prev in rows:
        url = quality.canonical_job_url(url)
        target = url
        if kind == "easy_apply" or "linkedin.com" in (urlsplit(url).hostname or ""):
            from lifeos.jobs.resolve.aggregators import li_apply                # noqa: PLC0415 - LinkedIn: the public guest page carries the JD
            jid = li_apply.job_id(url)
            target = (li_apply.GUEST_API + jid) if jid else url
        result = read_page(target, title, lane_of.get(job_id))
        if result.get("reason") == "description_empty" and _prev == TRIED_REASON:
            result["reason"] = TRIED_REASON                          # stays marked: the rendered fetch already tried it
        result["final_url"] = url
        results.append((job_id, result))
        counts["outcome"][result["outcome"]] = counts["outcome"].get(result["outcome"], 0) + 1
        if result.get("source_kind"):
            counts["source"][result["source_kind"]] = counts["source"].get(result["source_kind"], 0) + 1
    _fallback(results, rows, counts, lane_of)
    counts["reader_misses"] = ats_detail.misses()
    counts["blocked_final"] = blocked_report(results, {job_id: url for job_id, url, _, _, _ in rows})
    counts["mismatch"] = mismatch_report(results, {r[0]: r[1] for r in rows}, source_of, {r[0]: r[4] for r in rows})
    if live and results:
        with store.connect() as connection:
            for job_id, result in results:
                save(connection, job_id, result)
    counts["saved"] = bool(live)
    return counts


def _fallback(results, rows, counts, lane_of=None):
    """Rendered-fetch fallback (D12 amendment): ONE free TinyFish Fetch of an ALREADY-RESOLVED final URL when plain HTTP / the ATS API gave no usable page.
    It reads JD, date and liveness evidence only: no clicking, signing in, searching, link traversal, or choosing another vacancy, and it never
    changes final_apply_url. Counted against the daily cap BEFORE sending."""
    titles = {job_id: (url, title) for job_id, url, title, _, _ in rows}
    tried = {job_id for job_id, _, _, _, prev in rows if prev == TRIED_REASON}
    blocked = [i for i, (job_id, r) in enumerate(results) if r["outcome"] == "blocked" and job_id not in tried]
    if not blocked:
        return
    try:
        with store.connect() as connection:
            allowed = usage.reserve(connection, len(blocked))
    except store.StoreError:
        return
    counts["fallback_attempted"] = min(allowed, len(blocked))       # the rest waited on the daily Fetch cap
    pacer, done = usage.Pacer(), 0
    for start in range(0, allowed, limits.TINYFISH_FETCH_BATCH):
        batch = blocked[start:min(start + limits.TINYFISH_FETCH_BATCH, allowed)]
        asked = {i: ats_detail.workday_cxs_url(titles[results[i][0]][0]) for i in batch}   # Workday: ask its JSON endpoint
        urls = [asked[i] or titles[results[i][0]][0] for i in batch]
        pacer.wait()
        try:
            found, _errors = tinyfish.fetch_many(urls, fmt="html", links=False)
        except tinyfish.TinyFishError:
            counts["tinyfish"] = "error"
            return
        for i, url in zip(batch, urls):
            item = found.get(url)
            text = item.get("text") if item and isinstance(item.get("text"), str) else ""
            if asked[i] and text:
                job = ats_detail.parse_workday(text)
                if job:
                    job_id = results[i][0]
                    desc = jd.describe(job["html"], is_html=True)
                    results[i] = (job_id, finish(titles[job_id][1], desc, job["title"] or desc["full_text"][:300],
                                                 _parse_date(job["posted"]), "ats_api", None, (lane_of or {}).get(job_id)))
                    counts["outcome"]["blocked"] -= 1
                    counts["outcome"][results[i][1]["outcome"]] = counts["outcome"].get(results[i][1]["outcome"], 0) + 1
                    done += 1
                    continue
            if not text and results[i][1].get("reason") == "description_empty":
                results[i][1]["reason"] = TRIED_REASON
            if text:
                job_id = results[i][0]
                results[i] = (job_id, parse_html(url, titles[job_id][1], text, (lane_of or {}).get(job_id)))
                if results[i][1].get("reason") == "description_empty":
                    results[i][1]["reason"] = TRIED_REASON
                counts["outcome"]["blocked"] -= 1
                counts["outcome"][results[i][1]["outcome"]] = counts["outcome"].get(results[i][1]["outcome"], 0) + 1
                done += 1
    counts["tinyfish_recovered"] = done
