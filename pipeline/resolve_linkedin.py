"""LinkedIn rows -> final apply link WITHOUT logging in (D14).
Order: guest page (external URL, Easy Apply, closed) -> employer ATS board match by company/title/location -> pending."""
import time

from pipeline import ats_match, classify, li_apply
from pipeline.http import fetch


def read_guest(url, pause=1.2):
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


def resolve_rows(rows):
    """rows: [(id, source_url, company, title, location)] -> aligned result dicts."""
    results = []
    for _, url, *_ in rows:
        results.append(read_guest(url))
        if results[-1]["outcome"] == "rate_limited":
            results.extend({"outcome": "rate_limited"} for _ in rows[len(results):])
            break
    need = [i for i, r in enumerate(results) if r["outcome"] == "external_hidden"]
    hits = ats_match.match_many([(rows[i][2], rows[i][3], rows[i][4]) for i in need])
    for i, hit in zip(need, hits):
        results[i] = ({"outcome": "landed", "via": "ats_match", "kind": "ats", "url": hit[1]} if hit
                      else {"outcome": "no_board_match"})
    return results
