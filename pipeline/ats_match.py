"""Find a job on its employer's public ATS board by company + title (+ location). Plain HTTP, no login, no browser.

Used when an aggregator hides the employer link (LinkedIn external-apply, Lensa). Precision first: an exact normalized
title match, location-checked when several match; ambiguous or absent -> None (the job stays pending, never guessed).
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

from pipeline.http import fetch

SUFFIX = {"inc", "llc", "ltd", "corp", "corporation", "company", "co", "the", "group", "holdings", "technologies",
          "technology", "solutions", "services", "limited", "plc", "gmbh", "lp", "llp", "usa", "us"}
KINDS = ("greenhouse", "ashby", "lever", "smartrecruiters", "workable")


def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def slug_candidates(company):
    words = [w for w in norm(company).split() if w not in SUFFIX]
    if not words:
        return []
    out = ["".join(words), "-".join(words), words[0]]
    return list(dict.fromkeys(c for c in out if len(c) >= 3))[:3]


def _json(url):
    page = fetch(url, timeout=10, max_hops=2)
    if page.status != 200:
        return None
    try:
        return json.loads(page.html)
    except ValueError:
        return None


def board(kind, slug):
    """[(title, url, location_text)] from a public board API; [] when the board does not exist."""
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
        data = _json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
        return [(j.get("name"), f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                 " ".join(filter(None, [(j.get("location") or {}).get("city"), (j.get("location") or {}).get("country")])))
                for j in (data or {}).get("content", [])]
    if kind == "workable":
        data = _json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
        return [(j.get("title"), j.get("url"), " ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")])))
                for j in (data or {}).get("jobs", [])]
    return []


def boards_for(company):
    """All jobs on every public board found for the company: [(kind, title, url, location)]."""
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


def pick(jobs, title, location):
    """Exactly one confident board posting for (title, location), else None."""
    want = norm(title)
    same = [j for j in jobs if norm(j[1]) == want]
    if len(same) > 1:
        narrowed = [j for j in same if _location_ok(location, j[3])]
        same = narrowed if narrowed else []
        if len({j[2] for j in same}) > 1:
            return None
    return same[0] if same else None


def match_many(rows, workers=8):
    """rows: [(company, title, location)] -> aligned list of (kind, url) or None. One board fetch per company."""
    companies = list(dict.fromkeys(c for c, _, _ in rows if c))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        boards = dict(zip(companies, pool.map(boards_for, companies)))
    out = []
    for company, title, location in rows:
        hit = pick(boards.get(company, []), title, location) if company else None
        out.append((hit[0], hit[2]) if hit else None)
    return out
