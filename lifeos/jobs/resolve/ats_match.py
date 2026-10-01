"""Find a job on its employer's public ATS board by company + title (+ location). Plain HTTP, no login, no browser.

Used when an aggregator hides the employer link (LinkedIn external-apply, Lensa). Precision first: an exact normalized
title match, location-checked when several match; ambiguous or absent -> None (the job stays pending, never guessed).
"""
import difflib
import json
import re
from concurrent.futures import ThreadPoolExecutor

from lifeos.platform import limits
from lifeos.platform.http import fetch

SUFFIX = {"inc", "llc", "ltd", "corp", "corporation", "company", "co", "the", "group", "holdings", "technologies",
          "technology", "solutions", "services", "limited", "plc", "gmbh", "lp", "llp", "usa", "us"}
KINDS = ("greenhouse", "ashby", "lever", "smartrecruiters", "workable", "recruitee", "bamboohr", "breezy", "pinpoint")
SUBDOMAIN_KINDS = ("recruitee", "bamboohr", "breezy", "pinpoint")
SMARTRECRUITERS_PAGES = 5      # 100 postings per page; big employers have several hundred


def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def slug_candidates(company):
    words = [w for w in norm(company).split() if w not in SUFFIX]
    if not words:
        return []
    out = ["".join(words), "-".join(words), words[0]]
    return list(dict.fromkeys(c for c in out if len(c) >= 3))[:3]


def _json(url):
    try:
        page = fetch(url, timeout=limits.ATS_TIMEOUT_SECONDS, max_hops=2)
        return json.loads(page.html) if page.status == 200 else None
    except (ValueError, OSError):            # bad hostname (UnicodeError is a ValueError), bad JSON, network
        return None


def board(kind, slug):
    """[(title, url, location_text)] from a public board API; [] when the board does not exist."""
    if kind in SUBDOMAIN_KINDS and not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", slug):
        return []                                  # not a valid hostname label: no such board
    if kind == "greenhouse":
        data = _json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        return [(j.get("title"), j.get("absolute_url"), (j.get("location") or {}).get("name"))
                for j in (data or {}).get("jobs", [])]
    if kind == "ashby":
        data = _json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        return [(j.get("title"), j.get("jobUrl"), j.get("location")) for j in (data or {}).get("jobs", [])]
    if kind == "lever":
        data = _json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        return [(j.get("text"), j.get("hostedUrl"), (j.get("categories") or {}).get("location"))
                for j in data] if isinstance(data, list) else []
    if kind == "smartrecruiters":
        rows = []
        for page in range(SMARTRECRUITERS_PAGES):
            data = _json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={page * 100}")
            part = (data or {}).get("content", [])
            rows += [(j.get("name"), f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                      " ".join(filter(None, [(j.get("location") or {}).get("city"), (j.get("location") or {}).get("country")])))
                     for j in part]
            if len(part) < 100:
                break
        return rows
    if kind == "workable":
        data = _json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
        return [(j.get("title"), j.get("url"), " ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")])))
                for j in (data or {}).get("jobs", [])]
    if kind == "recruitee":
        data = _json(f"https://{slug}.recruitee.com/api/offers/")
        return [(j.get("title"), j.get("careers_url"), " ".join(filter(None, [j.get("city"), j.get("country")])))
                for j in (data or {}).get("offers", [])]
    if kind == "bamboohr":
        data = _json(f"https://{slug}.bamboohr.com/careers/list")
        return [(j.get("jobOpeningName"), f"https://{slug}.bamboohr.com/careers/{j.get('id')}",
                 " ".join(filter(None, [(j.get("location") or {}).get("city"), (j.get("location") or {}).get("state")])))
                for j in (data or {}).get("result", [])]
    if kind == "breezy":
        data = _json(f"https://{slug}.breezy.hr/json")
        return [(j.get("name"), j.get("url"), (j.get("location") or {}).get("name"))
                for j in data] if isinstance(data, list) else []
    if kind == "pinpoint":
        data = _json(f"https://{slug}.pinpointhq.com/postings.json")
        return [(j.get("title"), j.get("url"), (j.get("location") or {}).get("name"))
                for j in (data or {}).get("data", [])]
    return []


def boards_for(company):
    """All jobs on every public board found for the company: [(kind, title, url, location)]. Never raises."""
    try:
        return _boards_for(company)
    except Exception:                              # noqa: BLE001 - one odd company must not stop the stage
        return []


def _boards_for(company):
    found = []
    for slug in slug_candidates(company):
        for kind in KINDS:
            rows = board(kind, slug)
            if rows:
                found.extend((kind, t, u, loc) for t, u, loc in rows if t and u)
        if found:
            break
    return found


def _location_ok(want, have):
    want, have = norm(want), norm(have)
    if not want or not have:
        return None
    tokens = {t for t in want.split() if len(t) > 2 and t not in {"united", "states", "remote", "area", "metropolitan"}}
    return bool(tokens & set(have.split())) or ("remote" in want and "remote" in have)


def why(jobs, title, location):
    """('hit', job) | ('no_board',) | ('no_title',) | ('ambiguous',)."""
    if not jobs:
        return ("no_board",)
    want = norm(title)
    same = [j for j in jobs if norm(j[1]) == want]
    if not same:                                   # near-exact only (punctuation/level words aside), and unique
        close = [j for j in jobs if difflib.SequenceMatcher(None, norm(j[1]), want).ratio() >= 0.93]
        same = close if len(close) == 1 else []
    if not same:
        return ("no_title",)
    if len(same) > 1:
        narrowed = [j for j in same if _location_ok(location, j[3])]
        if not narrowed or len({j[2] for j in narrowed}) > 1:
            return ("ambiguous",)
        same = narrowed
    return ("hit", same[0])


def pick(jobs, title, location):
    """Exactly one confident board posting for (title, location), else None."""
    verdict = why(jobs, title, location)
    return verdict[1] if verdict[0] == "hit" else None


def match_many(rows, workers=limits.ATS_WORKERS):
    """rows: [(company, title, location)] -> aligned list of (kind, url) or None. One board fetch per company."""
    companies = list(dict.fromkeys(c for c, _, _ in rows if c))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        boards = dict(zip(companies, pool.map(boards_for, companies)))
    out = []
    for company, title, location in rows:
        verdict = why(boards.get(company, []), title, location) if company else ("no_company",)
        out.append((verdict[1][0], verdict[1][2]) if verdict[0] == "hit" else verdict[0])
    return out
