"""Enrich stage: RESOLVED rows -> read the FINAL page for description, real posting date, liveness, and a match check.

Source order for the description: ATS API (Greenhouse, Lever) -> schema.org JobPosting JSON-LD -> readable page text.
Outcomes: READY (fresh, live, matches) | CLOSED | EXCLUDED_STALE (>14 days, known) | back to NEW (page does not match the
job: a wrong link is never published) | pending with a fixed reason when the page cannot be read (retried later).
Plain HTTP only. Logs: counts only.
"""
from datetime import date, datetime, timedelta, timezone
import json
import re
from urllib.parse import urlsplit

from pipeline import ats_match, budget, jd, jsonld, limits, store, tinyfish
from pipeline.http import fetch

MAX_AGE_DAYS = 14
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
            if page.status == 200:
                try:
                    data = json.loads(page.html)
                except ValueError:
                    return None
                posted = str(data.get("first_published") or data.get("updated_at") or "")[:10]
                return {"title": data.get("title"), "html": data.get("content") or "", "posted": posted}
    if host == "jobs.lever.co" and len(segs) >= 2:
        page = fetch(f"https://api.lever.co/v0/postings/{segs[0]}/{segs[1]}", timeout=limits.ATS_TIMEOUT_SECONDS, max_hops=2)
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


def read_page(url, title):
    """Facts for one final URL. Never raises; fixed outcome codes."""
    api = _api_job(url)
    if api:
        text_source = api.get("plain") or api["html"]
        desc = jd.describe(text_source, is_html=not api.get("plain"))
        found_title, posted, source_kind = api.get("title"), _parse_date(api.get("posted")), "ats_api"
        valid_through = None
    else:
        page = fetch(url, timeout=15, max_hops=6)
        if page.status in (404, 410):
            return {"outcome": "closed"}
        if page.status != 200 or not page.html:
            return {"outcome": "blocked", "reason": f"http_{page.status or 0}"}
        return parse_html(url, title, page.html)
    return finish(title, desc, found_title, posted, source_kind, valid_through)


def parse_html(url, title, html):
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
        desc, source_kind, posted, valid_through = jd.describe(body_text, is_html=False), "page_text", None, None
        found_title = (re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I) or [None, ""])[1]
    return finish(title, desc, found_title, posted, source_kind, valid_through)


def finish(title, desc, found_title, posted, source_kind, valid_through):
    if len(desc["full_text"]) < 200:
        return {"outcome": "blocked", "reason": "description_empty"}
    if not _title_ok(title, found_title, desc["full_text"][:1500] if source_kind == "page_text" else ""):
        return {"outcome": "mismatch"}
    today = _now().date()
    if valid_through and valid_through < today:
        return {"outcome": "closed"}
    if posted and (today - posted) > timedelta(days=MAX_AGE_DAYS):
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
            posted = result.get("posted")
            age = (now.date() - posted).days if posted else None
            cursor.execute("UPDATE v7_jobs SET status=%s, posted_date=%s, posted_source=%s, "
                           "posted_age_days=COALESCE(%s, posted_age_days), unresolved_reason=NULL, updated_at=%s WHERE id=%s",
                           ("READY" if outcome == "ready" else "EXCLUDED_STALE", posted,
                            "employer" if posted else None, age, now, job_id))
        elif outcome == "closed":
            cursor.execute("UPDATE v7_jobs SET status='CLOSED', unresolved_reason='closed', updated_at=%s WHERE id=%s",
                           (now, job_id))
        elif outcome == "mismatch":                      # wrong link: take it back, never publish it
            cursor.execute("UPDATE v7_jobs SET status='NEW', final_apply_url=NULL, apply_kind=NULL, "
                           "unresolved_reason='link_mismatch', updated_at=%s WHERE id=%s", (now, job_id))
        else:
            cursor.execute("UPDATE v7_jobs SET unresolved_reason=%s, updated_at=%s WHERE id=%s",
                           ((result.get("reason") or "blocked")[:100], now, job_id))


def run(limit, live):
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, final_apply_url, title, apply_kind FROM v7_jobs WHERE status='RESOLVED' "
                           "AND final_apply_url IS NOT NULL ORDER BY last_seen DESC LIMIT %s", (limit,))
            rows = [tuple(r) for r in cursor.fetchall()]
    counts = {"picked": len(rows), "outcome": {}, "source": {}}
    results = []
    for job_id, url, title, kind in rows:
        target = url
        if kind == "easy_apply" or "linkedin.com" in (urlsplit(url).hostname or ""):
            from pipeline import li_apply                # noqa: PLC0415 - LinkedIn: the public guest page carries the JD
            jid = li_apply.job_id(url)
            target = (li_apply.GUEST_API + jid) if jid else url
        result = read_page(target, title)
        results.append((job_id, result))
        counts["outcome"][result["outcome"]] = counts["outcome"].get(result["outcome"], 0) + 1
        if result.get("source_kind"):
            counts["source"][result["source_kind"]] = counts["source"].get(result["source_kind"], 0) + 1
    _fallback(results, rows, counts)
    if live and results:
        with store.connect() as connection:
            for job_id, result in results:
                save(connection, job_id, result)
    counts["saved"] = bool(live)
    return counts


def _fallback(results, rows, counts):
    """Pages that refuse plain HTTP: free TinyFish Fetch (real browser), counted against the daily cap BEFORE sending."""
    titles = {job_id: (url, title) for job_id, url, title, _ in rows}
    blocked = [i for i, (_, r) in enumerate(results) if r["outcome"] == "blocked"
               and (r.get("reason") or "") != "description_empty"]
    if not blocked:
        return
    try:
        with store.connect() as connection:
            allowed = budget.reserve(connection, len(blocked))
    except store.StoreError:
        return
    pacer, done = budget.Pacer(), 0
    for start in range(0, allowed, limits.TINYFISH_FETCH_BATCH):
        batch = blocked[start:min(start + limits.TINYFISH_FETCH_BATCH, allowed)]
        urls = [titles[results[i][0]][0] for i in batch]
        pacer.wait()
        try:
            found, _errors = tinyfish.fetch_many(urls, fmt="html", links=False)
        except tinyfish.TinyFishError:
            counts["tinyfish"] = "error"
            return
        for i, url in zip(batch, urls):
            item = found.get(url)
            text = item.get("text") if item and isinstance(item.get("text"), str) else ""
            if text:
                job_id = results[i][0]
                results[i] = (job_id, parse_html(url, titles[job_id][1], text))
                counts["outcome"]["blocked"] -= 1
                counts["outcome"][results[i][1]["outcome"]] = counts["outcome"].get(results[i][1]["outcome"], 0) + 1
                done += 1
    counts["tinyfish_recovered"] = done
