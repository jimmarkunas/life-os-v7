"""Title watch (D123): roles a company's own careers site lists but its board listing does not carry (Revolut shows 334 roles on its list page while others
are live on revolut.com only).

For each registry source with `title_watch`, search the company's own site (free TinyFish Search, restricted to its domains) for each watched title, keep the
result pages that are job pages on that site, whose title matches a watched title and that V7 does not already hold, and add them to Jobs OS.

The page text of such a role usually cannot be read from the runner (403), so what is stored is the title and the search snippet and the role is marked by its
source (`web:titlewatch:<source id>`): Fit scores the title, the lane never lets it be a Go, and it reaches the board as Review ("found by title search;
description not read") for Jim to decide from the link. A role not seen by the search for EXPIRE_DAYS is closed. Counts only in the log."""
from datetime import datetime, timedelta, timezone
import re
from urllib.parse import urlsplit

from lifeos.jobs import enrich, intake, jd, store
from lifeos.platform.tinyfish import TinyFishError
from lifeos.sources.web import registry

LANE = "Scale-Up"
PREFIX = "web:titlewatch:"
MAX_QUERIES_PER_RUN = 14                  # the search key is shared (500 an hour; Lensa and LinkedIn use 400): pairs rotate by the hour
MAX_NEW_PER_RUN = 10
EXPIRE_DAYS = 10
DEFAULT_TITLES = ("Product Owner", "Head of Product", "Product Manager", "Program Manager", "Project Manager", "Campaign Creative Lead",
                  "Partnerships Manager", "Operations Manager", "Strategy & Operations Manager")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def watched(sources):
    return [s for s in sources if s.get("enabled", True) and isinstance(s.get("title_watch"), dict) and s["title_watch"].get("domains")]


def pairs(sources):
    """[(source, title)] in a fixed order."""
    out = []
    for source in sorted(watched(sources), key=lambda s: s["id"]):
        for title in source["title_watch"].get("titles") or DEFAULT_TITLES:
            out.append((source, title))
    return out


def window(all_pairs, now, size=MAX_QUERIES_PER_RUN):
    """The pairs searched this hour: a rotating slice, so every pair is searched in turn within the key's budget."""
    if len(all_pairs) <= size:
        return list(all_pairs)
    start = (int(now.replace(tzinfo=timezone.utc).timestamp() // 3600) * size) % len(all_pairs)
    return [all_pairs[(start + i) % len(all_pairs)] for i in range(size)]


def matches(title, titles):
    folded = (title or "").casefold()
    return any(t.casefold() in folded for t in titles)


def role_title(raw, titles):
    """The role title out of a search result title ("Product Owner (Crypto) | Revolut", "Revolut - Careers: Head of Product"): the first part that names a
    watched title, so the brand and generic words around it never matter."""
    for part in re.split(r"\s[|–—-]\s|:\s", raw or ""):
        if part.strip() and matches(part, titles):
            return part.strip()[:200]
    return ""


def token(url):
    """What identifies the role inside its URL: the uuid when there is one, else the last path segment."""
    found = UUID.findall(url or "")
    if found:
        return found[-1].lower()
    segments = [x for x in urlsplit(url or "").path.split("/") if x]
    return segments[-1].lower() if segments else ""


def candidates(source, results):
    """[(url, title, snippet)] job pages on the company's own domains, whose title matches a watched title."""
    cfg = source["title_watch"]
    titles = cfg.get("titles") or DEFAULT_TITLES
    out = []
    for r in results:
        url = r.get("url") or ""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not any(host == d or host.endswith("." + d) for d in cfg["domains"]):
            continue
        if cfg.get("path") and cfg["path"] not in parts.path:
            continue
        title = role_title(r.get("title"), titles)
        if title and len(token(url)) >= 6:
            out.append((url, title, (r.get("snippet") or "").strip()))
    return out


def _stored(cursor, url):
    tok = token(url)
    cursor.execute("SELECT id FROM v7_jobs WHERE source_url LIKE %s OR final_apply_url LIKE %s LIMIT 1", (f"%{tok}%", f"%{tok}%"))
    return cursor.fetchone() is not None


def _location(title, snippet):
    return "London" if re.search(r"\blondon\b", f"{title} {snippet}", re.I) else ""


def admit(connection, source, url, title, snippet, now):
    """One found role into Jobs OS as a title-only READY job. -> True when it is new."""
    with connection.cursor() as cursor:
        key, is_new = intake.add_job(cursor, {
            "url": url, "status": "RESOLVED", "title": title, "company": source["company"], "location": _location(title, snippet), "salary": None,
            "source": PREFIX + source["id"], "provider": "Title Watch", "lane": LANE, "age_days": None, "received": now, "provider_score": None, "posted": None}, now)
        cursor.execute("SELECT id FROM v7_jobs WHERE dedupe_key=%s", (key,))
        found = cursor.fetchone()
        if not found or not is_new:
            return False
        job_id = found[0]
        cursor.execute("UPDATE v7_jobs SET final_apply_url=%s, apply_kind='ats', route_evidence=%s, updated_at=%s WHERE id=%s",
                       (url, "Scale-up:POSITIVE", now, job_id))
    desc = jd.describe(f"{title}\n{snippet}".strip(), is_html=False)
    enrich.save(connection, job_id, {"outcome": "ready", "description": desc, "source_kind": "title_watch", "final_url": url})
    return True


def expire(connection, now):
    with connection.cursor() as cursor:
        cursor.execute("UPDATE v7_jobs SET status='CLOSED', unresolved_reason='title_watch_unseen', updated_at=%s WHERE source LIKE %s AND last_seen < %s"
                       " AND status NOT IN ('CLOSED','DUPLICATE','PURGED')", (now, PREFIX + "%", now - timedelta(days=EXPIRE_DAYS)))
        return int(cursor.rowcount or 0)


def run(limit, live, search=None, connect=None, sources=None, now=None):
    from lifeos.platform import tinyfish_search                                             # noqa: PLC0415
    search = search or tinyfish_search.search
    now = now or _now()
    sources = sources if sources is not None else registry.load(registry.PATHS["Scale-Up"])
    todo = window(pairs(sources), now)
    counts = {"sources": len(watched(sources)), "queries": 0, "errors": 0, "hits": 0, "known": 0, "new": 0, "added": 0, "expired": 0}
    found = []
    for source, title in todo:
        cfg = source["title_watch"]
        counts["queries"] += 1
        try:
            results = search(f"{source['company'].split(' Ltd')[0]} {title} careers", include_domains=cfg["domains"])
        except TinyFishError:
            counts["errors"] += 1
            continue
        for url, role, snippet in candidates(source, results):
            if url not in [f[1] for f in found]:
                found.append((source, url, role, snippet))
    counts["hits"] = len(found)
    if counts["queries"] and counts["errors"] == counts["queries"]:
        raise TinyFishError("TITLEWATCH_SEARCH_FAILED")
    with (connect or store.connect)() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            fresh = []
            for source, url, role, snippet in found:
                if _stored(cursor, url):
                    counts["known"] += 1
                    cursor.execute("UPDATE v7_jobs SET last_seen=%s WHERE (source_url LIKE %s) AND source LIKE %s", (now, f"%{token(url)}%", PREFIX + "%"))
                else:
                    fresh.append((source, url, role, snippet))
        counts["new"] = len(fresh)
        if live:
            for source, url, role, snippet in fresh[:MAX_NEW_PER_RUN]:
                counts["added"] += int(admit(connection, source, url, role, snippet, now))
            counts["expired"] = expire(connection, now)
    return counts
