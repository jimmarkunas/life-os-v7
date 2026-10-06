"""Counts-only diagnostic: why do parked Scale-Up roles keep failing their link check? Reads v7_jobs and each parked role's own public job page, writes nothing, prints no title, link or text.

Per source, one fixed-code line per role: the URL-shape problem (if any), the page status (Revolut also after the browser-style retry), whether the page carries a structured
job posting or a Greenhouse embed, how many of the role's title words the page holds (bucketed), and the description problem. The buckets show whether a role is a wrong link, a
page that does not carry the role's words, or a page we cannot read."""
from lifeos.jobs import enrich, jd, jsonld, quality, store
from lifeos.platform import impersonate
from lifeos.platform.http import fetch

REASONS = ("link_mismatch", "jd_listing", "jd_template")


def _bucket(share):
    return "w0" if share == 0 else "w_lt40" if share < 0.4 else "w_lt60" if share < 0.6 else "w_lt80" if share < 0.8 else "w_80plus"


def classify(url, title, fetch_page=fetch, impersonate_page=impersonate.fetch):
    problem = quality.link_problem(url)
    parts = ["shape_" + (problem or "ok")]
    page = fetch_page(url, timeout=15, max_hops=6)
    host_revolut = "revolut.com" in url.lower().split("/")[2] if "//" in url else False
    if page.status in (401, 403) and host_revolut:
        parts.append(f"plain_{page.status}")
        page = impersonate_page(url, warm_url="https://www.revolut.com/en-GB/careers/")
    parts.append(f"status_{page.status or 0}")
    if page.status != 200 or not page.html:
        return "/".join(parts)
    html = page.html
    posting = jsonld.job_posting(html)
    parts.append("jsonld" if posting else "no_jsonld")
    if enrich._greenhouse_embed(url, html):
        parts.append("gh_embed")
    parts.append(_bucket(enrich._title_words_in(title, html)))
    text = jd.html_to_text(html)
    parts.append("text_" + ("lt400" if len(text) < 400 else "ok"))
    parts.append("jd_" + (quality.jd_problem(text) or "ok"))
    return "/".join(parts)


def run(limit, live, fetch_page=fetch, impersonate_page=impersonate.fetch):
    out = {"by_source": {}, "checked": 0, "blocked_titles": []}
    with store.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT source, title, COALESCE(final_apply_url, source_url), unresolved_reason FROM v7_jobs "
                       "WHERE source LIKE 'web:su-%%' AND status='HOLD' AND (unresolved_reason IN ('link_mismatch','jd_listing','jd_template') OR unresolved_reason LIKE 'http_40%%') "
                       "AND COALESCE(final_apply_url, source_url) IS NOT NULL LIMIT 60")
        rows = cursor.fetchall()
    for source, title, url, reason in rows:
        key = f"{reason}|" + classify(url, title or "", fetch_page, impersonate_page)
        bucket = out["by_source"].setdefault(source, {})
        bucket[key] = bucket.get(key, 0) + 1
        out["checked"] += 1
        if "revolut" in source and str(reason).startswith("http_40"):
            out["blocked_titles"].append(title)                  # public job titles of the blocked Revolut roles only, so each can go on Jim's keep list (D124)
    return out
