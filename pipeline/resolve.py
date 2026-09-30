"""Stage 5 - resolve: find each job's final apply URL and description.

`--probe` is READ-ONLY: it samples NEW jobs per source, runs the resolution chain, and prints COUNTS ONLY
(no URLs, hosts, titles) so we can measure what works from CI before building the writer. Public repo.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import re
import sys

from pipeline import budget, classify, jsonld, store, tinyfish
from pipeline.http import MOBILE_UA, fetch

NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def jobright_target(html):
    """Employer/ATS URL Jobright embeds in its page state (originalUrl / applyLink), if present."""
    match = NEXT_DATA.search(html or "")
    if not match:
        return None, False
    try:
        job = json.loads(match.group(1))["props"]["pageProps"]["dataSource"]["jobResult"]
    except (KeyError, TypeError, ValueError):
        return None, True
    for key in ("originalUrl", "applyLink"):
        value = job.get(key) if isinstance(job, dict) else None
        if isinstance(value, str) and value.startswith("http"):
            return value, True
    return None, True


def _status_class(code):
    return "err" if not code else f"{code // 100}xx"


GUEST_API = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/"


def linkedin_job_id(url):
    match = re.search(r"/jobs/view/(\d+)", url or "")
    return match.group(1) if match else None


def probe_linkedin(url):
    """Facts from LinkedIn's public guest endpoint + canonical page (markers only)."""
    facts = {}
    job_id = linkedin_job_id(url)
    guest = fetch(GUEST_API + job_id) if job_id else fetch(url)
    page = fetch(url)
    facts["guest_codes"] = ">".join(map(str, guest.codes)) or "none"
    facts["page_codes"] = ">".join(map(str, page.codes)) or "none"
    for name, html in (("guest", guest.html), ("page", page.html)):
        facts[name + "_offsite"] = "apply-link-offsite" in html or "externalApply" in html
        facts[name + "_onsite"] = "apply-link-onsite" in html
        ext = re.search(r"externalApply/\d+\?url=([^&\"'\s]+)", html)
        facts[name + "_ext_url"] = bool(ext)
        if ext:
            from urllib.parse import unquote
            facts[name + "_ext_kind"] = classify.apply_kind(unquote(ext.group(1)))
        facts[name + "_desc"] = "show-more-less-html__markup" in html or "description__text" in html
        facts[name + "_posted"] = "posted-time-ago" in html or "datePosted" in html
        facts[name + "_apply_code"] = 'id="applyUrl"' in html
        facts[name + "_easy"] = bool(re.search(r"easy\s*apply", html, re.I))
        facts[name + "_authwall"] = "authwall" in html.lower() or "sign in to view" in html.lower()
        facts[name + "_len_bucket"] = str(min(len(html) // 20000, 5))
    return facts


SUFFIX = {"inc", "llc", "corp", "corporation", "ltd", "co", "company", "group", "technologies", "technology", "the", "plc", "lp", "holdings"}


def slug_candidates(company):
    words = [w for w in re.sub(r"[^a-z0-9 ]", " ", (company or "").lower()).split() if w not in SUFFIX]
    if not words:
        return []
    out = ["".join(words), "-".join(words), words[0]]
    return list(dict.fromkeys(c for c in out if len(c) >= 3))[:3]


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def board_jobs(kind, slug):
    """(title, url, date_hint) rows from a public ATS board API; [] when the board does not exist."""
    urls = {"greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            "lever": f"https://api.lever.co/v0/postings/{slug}?mode=json"}
    page = fetch(urls[kind], timeout=10, max_hops=2)
    if page.status != 200:
        return []
    try:
        data = json.loads(page.html)
    except ValueError:
        return []
    if kind == "greenhouse":
        return [(j.get("title"), j.get("absolute_url"), j.get("updated_at")) for j in data.get("jobs", [])]
    if kind == "ashby":
        return [(j.get("title"), j.get("jobUrl"), j.get("publishedAt")) for j in data.get("jobs", [])]
    return [(j.get("text"), j.get("hostedUrl"), j.get("createdAt")) for j in data] if isinstance(data, list) else []


def probe_ats_lookup(company, title):
    """Does the employer's public ATS board list this exact title? Counts-only facts."""
    facts = {"board_found": False, "title_match": False, "ats": ""}
    want = _norm(title)
    for slug in slug_candidates(company):
        for kind in ("greenhouse", "ashby", "lever"):
            rows = board_jobs(kind, slug)
            if not rows:
                continue
            facts["board_found"], facts["ats"] = True, kind
            if any(_norm(t) == want for t, _, _ in rows):
                facts["title_match"] = True
                return facts
    return facts


def linkedin_offsite_follow(html):
    """Follow LinkedIn's external-apply button (if the page has one) and classify where it lands."""
    import html as htmllib
    for tag in re.findall(r"<a\b[^>]*>", html or ""):
        if "apply-link-offsite" in tag or "externalApply" in tag:
            href = re.search(r'href="([^"]+)"', tag)
            if href:
                landed = fetch(htmllib.unescape(href.group(1)), timeout=10, max_hops=6)
                return {"follow_codes": ">".join(map(str, landed.codes)) or "none",
                        "follow_kind": classify.apply_kind(landed.final_url) if landed.codes else "none"}
    return {}


def probe_one(source, url, company="", title=""):
    """Follow the chain for one job; return a dict of small categorical facts."""
    facts = {"first": "", "hops": 0, "final_kind": "", "next_data": False, "target": False, "jsonld": False,
             "date": False, "desc": False, "easy_apply_marker": False, "offsite_marker": False}
    first = fetch(url)
    facts["first"] = ">".join(map(str, first.codes)) or ("err:" + first.error)
    facts["first_kind"] = classify.apply_kind(first.final_url) if first.status or first.codes else "none"
    facts["hops"] = first.hops
    final = first
    if source == "jobright" and first.html:
        target, facts["next_data"] = jobright_target(first.html)
        facts["target"] = bool(target)
        if target:
            final = fetch(target)
    if source == "linkedin-alerts":
        facts.update(probe_linkedin(url))
        facts.update(linkedin_offsite_follow(fetch(url).html))
    if source == "lensa":
        facts.update({"ats_" + k: v for k, v in probe_ats_lookup(company, title).items()})
    if source == "lensa" and first.status == 403:
        for label, hdrs in (("referer", {"Referer": "https://email.lensa.com/"}), ("mobile", {"User-Agent": MOBILE_UA}),
                            ("bare", {"User-Agent": "curl/8.5.0", "Accept": "*/*"})):
            retry = fetch(first.final_url, headers=hdrs)
            facts["lensa_" + label] = ">".join(map(str, retry.codes)) or "none"
    if source == "jobright":
        facts["jr_len_bucket"] = str(min(len(first.html) // 20000, 5))
        facts["jr_blocked_marker"] = bool(re.search(r"just a moment|captcha|access denied", first.html or "", re.I))
        posting = jsonld.job_posting(first.html)
        facts["jr_ld_has_url"] = bool(posting and posting.get("url"))
        facts["jr_ld_has_org"] = bool(posting and posting.get("hiringOrganization"))
    facts["final_kind"] = classify.apply_kind(final.final_url) if final.status else "unreachable"
    posting = jsonld.job_posting(final.html)
    facts["jsonld"] = posting is not None
    facts["date"] = jsonld.posted_date(posting) is not None
    facts["desc"] = bool(posting and len(str(posting.get("description") or "")) > 200)
    return facts


def probe(connection, per_source, workers):
    sample = []
    with connection.cursor() as cursor:
        for source in ("lensa", "jobright", "linkedin-alerts"):
            cursor.execute("SELECT source, source_url, company, title FROM v7_jobs WHERE status='NEW' AND source=%s "
                           "ORDER BY RAND() LIMIT %s", (source, per_source))
            sample += cursor.fetchall()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda row: (row[0], probe_one(row[0], row[1], row[2], row[3])), sample))
    report = {}
    for source in ("lensa", "jobright", "linkedin-alerts"):
        rows = [f for s, f in results if s == source]
        keys = sorted({k for r in rows for k in r})
        report[source] = {"n": len(rows)}
        for key in keys:
            values = [r.get(key) for r in rows]
            if all(isinstance(v, bool) or v is None for v in values):
                report[source][key] = sum(1 for v in values if v)
            elif key != "hops":
                report[source][key] = dict(Counter(str(v) for v in values))
    return report


def summarize_tinyfish(source, urls, results, errors):
    """Counts-only facts about a TinyFish Fetch batch (no URLs, hosts, or text)."""
    facts = {"n": len(urls), "ok": 0, "errors": {}, "final_kind": {}, "text_bucket": {}, "published_date": 0,
             "jsonld_posting": 0, "jsonld_desc": 0, "links_bucket": {}, "external_link_pages": 0}
    for err in errors:
        code = str(err.get("code") or err.get("error") or err.get("type") or "unknown")[:30]
        facts["errors"][code] = facts["errors"].get(code, 0) + 1
    bump = lambda bucket, key: bucket.__setitem__(key, bucket.get(key, 0) + 1)
    for url in urls:
        item = results.get(url)
        if not item:
            continue
        facts["ok"] += 1
        bump(facts["final_kind"], classify.apply_kind(item.get("final_url") or url))
        text = item.get("text") if isinstance(item.get("text"), str) else ""
        bump(facts["text_bucket"], str(min(len(text) // 10000, 5)))
        facts["published_date"] += 1 if item.get("published_date") else 0
        posting = jsonld.job_posting(text)
        facts["jsonld_posting"] += 1 if posting else 0
        facts["jsonld_desc"] += 1 if posting and len(str(posting.get("description") or "")) > 200 else 0
        links = item.get("links") or []
        bump(facts["links_bucket"], str(min(len(links) // 25, 4)))
        facts["external_link_pages"] += 1 if any(
            classify.apply_kind(link) in ("employer", "ats") and "linkedin" not in link and "lensa" not in link
            and "jobright" not in link for link in links if isinstance(link, str) and link.startswith("http")) else 0
    return facts


def probe_tinyfish(connection, per_source):
    report, pacer = {}, budget.Pacer()
    with connection.cursor() as cursor:
        for source in ("lensa", "jobright", "linkedin-alerts"):
            cursor.execute("SELECT source_url FROM v7_jobs WHERE status='NEW' AND source=%s ORDER BY RAND() LIMIT %s",
                           (source, min(per_source, tinyfish.MAX_URLS)))
            urls = [row[0] for row in cursor.fetchall()]
            urls = urls[:budget.reserve(connection, len(urls))]          # daily free-tier guard, counted BEFORE sending
            if not urls:
                report[source] = {"n": 0, "skipped": "daily_cap_reached"}
                continue
            pacer.wait()
            results, errors = tinyfish.fetch_many(urls)
            report[source] = summarize_tinyfish(source, urls, results, errors)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--probe-tinyfish", action="store_true")
    parser.add_argument("--per-source", type=int, default=12)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)
    if not (args.probe or args.probe_tinyfish):
        print("only --probe / --probe-tinyfish are implemented so far", file=sys.stderr)
        return 1
    try:
        with store.connect() as connection:
            store.ensure_schema(connection)
            if args.probe_tinyfish:
                print("probe-tinyfish:", json.dumps(probe_tinyfish(connection, args.per_source), sort_keys=True))
            else:
                print("probe:", json.dumps(probe(connection, args.per_source, args.workers), sort_keys=True))
    except (store.StoreError, tinyfish.TinyFishError) as error:
        print(f"RESOLVE FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
