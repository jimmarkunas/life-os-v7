"""Resolve anonymous Dice alert redirects to canonical Dice job pages."""
import re
import time
from urllib.parse import urlsplit

from lifeos.jobs import classify
from lifeos.jobs.resolve import stage
from lifeos.platform import http, limits

TRACKING_HOST = "elinks.dice.com"
TRACKING_PATH = re.compile(r"^/a/sc/.+")
JOB_PATH = re.compile(r"^/(?:job-detail|jobs/detail)/([A-Za-z0-9_-]+)/?$")
CLOSED_PAGE = re.compile(r"no longer accepting|job (?:is )?closed|posting (?:has )?expired|position (?:is )?filled", re.I)
REDIRECT_STATUSES = (301, 302, 303, 307, 308)


def resolve(url):
    """Follow one Dice email redirect and accept only a Dice job-detail URL."""
    try:
        source = urlsplit(url or "")
    except ValueError:
        return {"outcome": "not_a_job_page"}
    if source.scheme != "https" or source.hostname != TRACKING_HOST or not TRACKING_PATH.match(source.path):
        return {"outcome": "not_a_job_page"}

    page = http.fetch(url, timeout=limits.DICE_TIMEOUT_SECONDS, max_hops=limits.DICE_MAX_HOPS)
    if page.status == 429:
        return {"outcome": "rate_limited"}
    if page.status == 0:
        return {"outcome": "expired_link" if page.error == "too_many_redirects" else "network_error"}
    if page.status in REDIRECT_STATUSES:
        return {"outcome": "expired_link"}

    try:
        final = urlsplit(page.final_url or "")
    except ValueError:
        return {"outcome": "not_a_job_page"}
    host = (final.hostname or "").lower()
    if host == TRACKING_HOST:
        return {"outcome": "http_403" if page.status == 403 else "expired_link"}
    if host not in ("dice.com", "www.dice.com"):
        return {"outcome": "not_a_job_page"}
    match = JOB_PATH.fullmatch(final.path)
    if not match:
        return {"outcome": "not_a_job_page"}
    if page.status in (404, 410):
        return {"outcome": "closed"}
    if page.status != 200:
        return {"outcome": f"http_{page.status}"}
    if CLOSED_PAGE.search(page.html or ""):
        return {"outcome": "closed"}
    canonical = f"https://www.dice.com/job-detail/{match.group(1)}"
    return {"outcome": "landed", "via": "tracking_redirect", "kind": classify.apply_kind(canonical), "url": canonical}


def make_resolver():
    """Keep pacing and stop-on-block state across all batches in one stage run."""
    last_request = [None]
    stopped = [None]

    def resolve_rows(rows):
        results = []
        for row in rows:
            if stopped[0]:
                results.append({"outcome": "deferred"})          # not tried, so not an attempt: a block must not park the rest on HOLD
                continue
            if last_request[0] is not None:
                remaining = limits.DICE_GAP_SECONDS - (time.monotonic() - last_request[0])
                if remaining > 0:
                    time.sleep(remaining)
            result = resolve(row[1])
            last_request[0] = time.monotonic()
            results.append(result)
            if result["outcome"] in ("rate_limited", "http_403"):
                stopped[0] = result["outcome"]
        return results

    return resolve_rows


def run(limit, live):
    """Bound and execute the resolver through Jobs OS's shared batched deadline."""
    bounded = min(max(0, limit), limits.DICE_PER_RUN)
    counts = stage.run_rows("dice", bounded, live, make_resolver(), batch=5,
                            deadline_minutes=limits.DICE_DEADLINE_MINUTES)
    counts["rate_limited"] = counts["why"].get("rate_limited", 0)
    return {key: counts[key] for key in ("picked", "resolved", "closed", "duplicate", "pending", "rate_limited")}
