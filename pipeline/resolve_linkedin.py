"""LinkedIn rows -> final apply link WITHOUT logging in (D14).
Order: guest page (external URL, Easy Apply, closed) -> employer ATS board match by company/title/location -> pending."""
import time

from pipeline import ats_match, classify, li_apply, limits, search_match
from pipeline.http import fetch


def read_guest(url, pause=limits.LINKEDIN_GUEST_GAP_SECONDS):
    jid = li_apply.job_id(url)
    if not jid:
        return {"outcome": "no_job_id"}
    time.sleep(pause)
    page = fetch(li_apply.GUEST_API + jid, timeout=15, max_hops=2)
    if page.status in (404, 410):
        return {"outcome": "closed"}
    if page.status == 429:
        return {"outcome": "rate_limited"}
    if page.status != 200:
        return {"outcome": f"guest_http_{page.status}"}
    kind, target = li_apply.read(page.html)
    if kind == "closed":
        return {"outcome": "closed"}
    if kind == "external" and target:
        return {"outcome": "landed", "via": "guest", "kind": classify.apply_kind(target), "url": target}
    if kind == "easy_apply":
        return {"outcome": "landed", "via": "easy_apply", "kind": "easy_apply", "url": url}
    return {"outcome": "external_hidden"}


SEARCH_LIMIT = limits.TINYFISH_SEARCH_PER_RUN


def resolve_rows(rows):
    """rows: [(id, source_url, company, title, location)] -> aligned result dicts."""
    results = []
    for _, url, *_ in rows:
        results.append(read_guest(url))
        if results[-1]["outcome"] == "rate_limited":
            results.extend({"outcome": "rate_limited"} for _ in rows[len(results):])
            break
    return match_rows(rows, results)


def match_rows(rows, results):
    """Fill every 'external_hidden' result: ATS board match, then free Search. Shared with Lensa."""
    need = [i for i, r in enumerate(results) if r["outcome"] == "external_hidden"]
    hits = ats_match.match_many([(rows[i][2], rows[i][3], rows[i][4]) for i in need])
    for i, hit in zip(need, hits):
        results[i] = ({"outcome": "no_match_" + hit} if isinstance(hit, str)
                      else {"outcome": "landed", "via": "ats_match", "kind": "ats", "url": hit[1]})
    searched = 0
    for i, result in enumerate(results):
        if not result["outcome"].startswith("no_match_") or searched >= SEARCH_LIMIT:
            continue
        searched += 1
        verdict = search_match.find(rows[i][2], rows[i][3])
        if verdict[0] != "hit":
            results[i] = {"outcome": results[i]["outcome"] + "+" + verdict[1]}
            continue
        gone = fetch(verdict[2], timeout=12, max_hops=4).status in (404, 410)      # terminal evidence: dead page = closed
        results[i] = ({"outcome": "closed"} if gone else
                      {"outcome": "landed", "via": "search", "kind": verdict[1], "url": verdict[2]})
    return results
