"""Lensa rows -> final apply link. First V1's mechanic: follow the card's own email tracking link and take the employer page it lands on, or
the ONE ATS link on the Lensa job page it lands on. Where Lensa blocks the runner (403) or the chain proves nothing, the card's company / title /
location are matched against the employer's public ATS boards, then the free Search API. A block or 429 stops the follows for the run."""
import html as htmllib
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

from lifeos.platform import http, limits, tinyfish_search
from lifeos.jobs import classify, lanes, quality
from lifeos.jobs.resolve import ats_match
from lifeos.jobs.resolve.aggregators import linkedin

TITLE_FIX_AT = datetime(2026, 10, 3, 22, 0)      # UTC: D80 shipped before this; a job that fails again after it is never requeued again


HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)


def _lensa_host(url):
    host = classify.host(url)
    return host == "lensa.com" or host.endswith(".lensa.com")


def follow(url):
    """One card's tracking link -> {'outcome': 'landed', ...} | {'outcome': 'external_hidden', 'redirect': code}. Fixed codes only."""
    if not url or not _lensa_host(url):
        return {"outcome": "external_hidden", "redirect": "no_link"}
    page = http.fetch(url, timeout=limits.LENSA_FOLLOW_TIMEOUT_SECONDS, max_hops=limits.LENSA_FOLLOW_MAX_HOPS, max_bytes=512_000)
    final = page.final_url or ""
    if page.status == 0 or page.status in (403, 429):
        return {"outcome": "external_hidden", "redirect": "blocked" if page.status else "network"}
    if not _lensa_host(final):                                               # the chain left Lensa: the employer's page itself
        if page.status == 200 and classify.apply_kind(final) != "aggregator" and not quality.link_problem(final):
            return {"outcome": "landed", "via": "tracking_redirect", "kind": classify.apply_kind(final), "url": final}
        return {"outcome": "external_hidden", "redirect": "elsewhere"}
    if page.status != 200:
        return {"outcome": "external_hidden", "redirect": "lensa_" + str(page.status)}
    links = {link for link in (urljoin(final, htmllib.unescape(h)) for h in HREF.findall(page.html or ""))
             if classify.apply_kind(link) == "ats" and not quality.link_problem(link)}
    if len(links) == 1:                                                      # exactly one ATS posting on the page (V1 _downstream_candidates, precision first)
        link = links.pop()
        return {"outcome": "landed", "via": "lensa_page", "kind": "ats", "url": link}
    return {"outcome": "external_hidden", "redirect": "lensa_page_" + ("none" if not links else "many")}


def make_resolver():
    """One search budget for the whole run, shared by every batch the stage hands in. `resolve_rows.redirect` counts what the follows did."""
    budget = {"left": limits.TINYFISH_SEARCH_PER_RUN_LENSA}
    tinyfish_search.solo_after(limits.TINYFISH_SEARCH_SOLO_AFTER_SECONDS)       # LinkedIn's lane is finished by then: use the full 27/min
    seen, stopped = {}, [False]

    def resolve_rows(rows):
        results = []
        for row in rows:
            result = {"outcome": "external_hidden", "redirect": "stopped"} if stopped[0] else follow(row[1])
            if not stopped[0]:
                time.sleep(limits.LENSA_FOLLOW_GAP_SECONDS)
            code = "landed_" + result["via"] if result["outcome"] == "landed" else result.pop("redirect")
            seen[code] = seen.get(code, 0) + 1
            stopped[0] = stopped[0] or code == "blocked"
            results.append(result)
        return linkedin.match_rows(rows, results, budget=budget)
    resolve_rows.redirect = seen
    return resolve_rows


SEEN_ELSEWHERE = ("RESOLVED", "READY", "PUBLISHED", "EXCLUDED_FIT", "EXCLUDED_STALE", "CLOSED")    # another producer already carried this opening to a result
SCREEN_WINDOW_DAYS = 60


def _opening(company, title):
    forms = ats_match.title_variants(title)
    return (ats_match.norm(company), forms[-1]) if forms and ats_match.norm(company) else None


def screen(cursor, live, now=None):
    """D88: spend no search on a Lensa job we can already settle. (1) The posted pay is explicit, in dollars and under the US floor: EXCLUDED_FIT
    (`screen_pay`). (2) The same company and title (aggregator noise removed, three words or more) is already a result from another producer in the last
    60 days (a board, Jobright, LinkedIn): DUPLICATE of it (`dup_other_source`), because the other copy is resolved, published, or excluded on the same posting.
    A job whose other copy is still NEW or on HOLD is not screened. Counts only."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    counts = {"screened_pay": 0, "screened_dup": 0}
    floor = lanes.POLICIES["US Remote"]
    cursor.execute("SELECT id, company, title, salary_text FROM v7_jobs WHERE source='lensa' AND status='NEW'")
    new = cursor.fetchall()
    cursor.execute("SELECT id, company, title FROM v7_jobs WHERE source<>'lensa' AND status IN %s AND first_seen >= %s",
                   (SEEN_ELSEWHERE, now - timedelta(days=SCREEN_WINDOW_DAYS)))
    elsewhere = {}
    for job_id, company, title in cursor.fetchall():
        key = _opening(company, title)
        if key and len(key[1].split()) >= 3:
            elsewhere.setdefault(key, job_id)
    for job_id, company, title, salary in new:
        pay, currency = lanes.parse_pay(salary)
        if pay is not None and currency == floor.currency and pay < floor.pay_floor:
            counts["screened_pay"] += 1
            if live:
                cursor.execute("UPDATE v7_jobs SET status='EXCLUDED_FIT', unresolved_reason='screen_pay', updated_at=%s WHERE id=%s", (now, job_id))
            continue
        key = _opening(company, title)
        other = elsewhere.get(key) if key and len(key[1].split()) >= 3 else None
        if other:
            counts["screened_dup"] += 1
            if live:
                cursor.execute("UPDATE v7_jobs SET status='DUPLICATE', repost_of=%s, unresolved_reason='dup_other_source', updated_at=%s WHERE id=%s",
                               (other, now, job_id))
    return counts


def requeue_held(cursor, cutoff=TITLE_FIX_AT):
    """One-time catch-up for D80 (decorated titles no longer defeat the board and search match): Lensa jobs parked on HOLD by a definite `no_match_*`
    BEFORE the fix, whose title carried noise the matcher now removes, go back to NEW for one more try. A job that fails again is stamped after the
    cutoff, so the timestamp (not a reason that the failure would overwrite) is what makes this once."""
    cursor.execute("SELECT id, title FROM v7_jobs WHERE source='lensa' AND status='HOLD' AND unresolved_reason LIKE 'no_match%%' AND updated_at < %s", (cutoff,))
    ids = [row[0] for row in cursor.fetchall() if len(ats_match.title_variants(row[1])) > 1]
    for job_id in ids:
        cursor.execute("UPDATE v7_jobs SET status='NEW', resolve_attempts=0, unresolved_reason='requeued_titles', updated_at=UTC_TIMESTAMP() WHERE id=%s", (job_id,))
    return len(ids)


resolve_rows = make_resolver()
