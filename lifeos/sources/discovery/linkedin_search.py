"""LinkedIn job search (the public guest search: no login, no browser) as a source beside the job-alert newsletter.

Rows have source `linkedin-search` and the newsletter's canonical URL, https://www.linkedin.com/jobs/view/<id>, so
a job both find is one row and a known job is left untouched. The hourly step runs after the resolver with a small
page allowance; the first refusal stops it and is named (`rate_limited_429`, `blocked_<status>`, `http_<status>`,
`network`, `unreadable_page`). A card is admitted only as a target-family job in the US (or plain remote) with no
office requirement; each rejection reason is counted.
"""

from datetime import date, datetime, timezone
import html as htmllib
import re
import time
from urllib.parse import urlencode

from lifeos.jobs import identity, intake, lanes, store
from lifeos.platform import limits
from lifeos.platform.http import fetch
from lifeos.sources.web import boards, suppress

SOURCE = "linkedin-search"
LANE = "US Remote"
SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
QUERIES = ("technical program manager", "program manager", "delivery manager", "implementation manager")
PAGE = 10

CARD = re.compile(r'<li>(.*?)</li>', re.S)
JOB_ID = re.compile(r'urn:li:jobPosting:(\d{6,})')
FIELD = {
    "title": re.compile(r'base-search-card__title">\s*(.*?)\s*<', re.S),
    "company": re.compile(r'base-search-card__subtitle">\s*(?:<a[^>]*>)?\s*(.*?)\s*<', re.S),
    "location": re.compile(r'job-search-card__location">\s*(.*?)\s*<', re.S),
}
POSTED = re.compile(r'datetime="(\d{4}-\d{2}-\d{2})"')


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def search_url(query, start):
    """Roles in the United States posted in the last 24 hours (f_TPR=r86400). No work-mode filter: the guest search
    ignores f_WT.
    """
    return SEARCH + "?" + urlencode({"keywords": query, "location": "United States", "f_TPR": "r86400", "start": start})


def parse(markup):
    """Guest search HTML -> cards [{id, url, title, company, location, posted}]; a card without a job id or title is
    skipped.
    """
    cards = []
    for block in CARD.findall(markup or ""):
        jid = JOB_ID.search(block)
        got = {k: rx.search(block) for k, rx in FIELD.items()}
        if not jid or not got["title"]:
            continue
        text = {k: htmllib.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else "" for k, m in got.items()}
        posted = POSTED.search(block)
        cards.append(
            {
                "id": jid.group(1),
                "url": "https://www.linkedin.com/jobs/view/" + jid.group(1),
                **text,
                "posted": date.fromisoformat(posted.group(1)) if posted else None,
            }
        )
    return cards


def refused(page):
    if page.status == 429:
        return "rate_limited_429"
    if page.status in (403, 999):
        return f"blocked_{page.status}"
    if page.status != 200:
        return f"http_{page.status}" if page.status else "network"
    return None


def read(fetcher=fetch, pause=limits.LINKEDIN_GUEST_GAP_SECONDS, pages=None):
    pages = limits.LINKEDIN_SEARCH_PAGES_PER_RUN if pages is None else pages
    counts = {"pages": 0, "cards": 0, "stopped": None}
    seen, cards, live_queries, start = set(), [], list(QUERIES), 0
    while live_queries and counts["pages"] < pages:
        for query in list(live_queries):
            if counts["pages"] >= pages:
                break
            time.sleep(pause)
            page = fetcher(search_url(query, start), timeout=15, max_hops=2)
            counts["pages"] += 1
            why = refused(page)
            if why:
                counts["stopped"], counts["cards"] = why, len(cards)
                return cards, counts
            found = parse(page.html)
            if not found and "<li" in (page.html or ""):
                counts["stopped"], counts["cards"] = "unreadable_page", len(cards)
                return cards, counts
            for card in found:
                if card["id"] not in seen:
                    seen.add(card["id"])
                    cards.append(card)
            if len(found) < PAGE:
                live_queries.remove(query)
        start += PAGE
    counts["cards"] = len(cards)
    return cards, counts


def screen(card):
    title, where = card["title"], card["location"] or ""
    tier, why = boards.role(title)
    if not tier:
        return "title_" + why
    place = boards.geography(where)
    if place == "non_us":
        return "non_us"
    tail = "_place_unresolved" if place == "unresolved" else ""
    mode = lanes.detect_work_mode(where, title)
    if mode in ("onsite", "hybrid"):
        return "not_remote_" + mode
    return ("admitted_remote_in_location" if mode == "remote" else "admitted_remote_assumed") + tail


def run(limit, live, now=None, fetcher=fetch, pause=limits.LINKEDIN_GUEST_GAP_SECONDS, connection=None):
    now = now or _now()
    cards, counts = read(fetcher, pause)
    counts["cards"] = len(cards)
    reasons = {c["id"]: screen(c) for c in cards}
    wanted = sorted(
        (c for c in cards if reasons[c["id"]].startswith("admitted_")),
        key=lambda c: reasons[c["id"]] != "admitted_remote_in_location",
    )
    counts.update(
        screen={r: list(reasons.values()).count(r) for r in sorted(set(reasons.values()))},
        new=0,
        already_known=0,
        tombstoned=0,
        over_allowance=0,
        saved=bool(live),
    )
    if not live or not wanted:
        counts["would_add"] = len(wanted) if not live else 0
        return counts
    room = min(limit, limits.LINKEDIN_SEARCH_NEW_PER_RUN)
    with connection or store.connect() as conn:
        if connection is None:
            store.ensure_schema(conn)
        with conn.cursor() as cursor:
            for card in wanted:
                cursor.execute("SELECT 1 FROM v7_jobs WHERE dedupe_key=%s", (identity.url_hash(card["url"]),))
                if cursor.fetchone():
                    counts["already_known"] += 1
                    continue
                if counts["new"] >= room:
                    counts["over_allowance"] += 1
                    continue
                _, is_new = intake.add_job(
                    cursor,
                    {
                        "url": card["url"],
                        "status": "NEW",
                        "title": card["title"],
                        "company": card["company"] or "Unknown",
                        "location": card["location"],
                        "salary": None,
                        "source": SOURCE,
                        "provider": "LinkedIn Jobs",
                        "lane": LANE,
                        "age_days": (now.date() - card["posted"]).days if card["posted"] else None,
                        "received": now,
                        "provider_score": None,
                        "posted": card["posted"],
                    },
                    now,
                )
                counts["new" if is_new else "tombstoned"] += 1
    return counts
