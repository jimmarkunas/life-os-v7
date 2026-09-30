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

from pipeline import classify, jsonld, store
from pipeline.http import fetch

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


def probe_one(source, url):
    """Follow the chain for one job; return a dict of small categorical facts."""
    facts = {"first": "", "hops": 0, "final_kind": "", "next_data": False, "target": False, "jsonld": False,
             "date": False, "desc": False, "easy_apply_marker": False, "offsite_marker": False}
    first = fetch(url)
    facts["first"] = _status_class(first.status) + (":" + first.error if first.error else "")
    facts["hops"] = first.hops
    final = first
    if source == "jobright" and first.html:
        target, facts["next_data"] = jobright_target(first.html)
        facts["target"] = bool(target)
        if target:
            final = fetch(target)
    if source == "linkedin-alerts" and first.html:
        facts["easy_apply_marker"] = "Easy Apply" in first.html
        facts["offsite_marker"] = "apply-link-offsite" in first.html
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
            cursor.execute("SELECT source, source_url FROM v7_jobs WHERE status='NEW' AND source=%s "
                           "ORDER BY RAND() LIMIT %s", (source, per_source))
            sample += cursor.fetchall()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda row: (row[0], probe_one(row[0], row[1])), sample))
    report = {}
    for source in ("lensa", "jobright", "linkedin-alerts"):
        rows = [f for s, f in results if s == source]
        tally = lambda key: dict(Counter(str(r[key]) for r in rows))
        report[source] = {"n": len(rows), "first_fetch": tally("first"), "final_kind": tally("final_kind"),
                          "avg_hops": round(sum(r["hops"] for r in rows) / max(1, len(rows)), 1),
                          **{k: sum(1 for r in rows if r[k]) for k in
                             ("next_data", "target", "jsonld", "date", "desc", "easy_apply_marker", "offsite_marker")}}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--per-source", type=int, default=12)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)
    if not args.probe:
        print("only --probe is implemented so far", file=sys.stderr)
        return 1
    try:
        with store.connect() as connection:
            store.ensure_schema(connection)
            print("probe:", json.dumps(probe(connection, args.per_source, args.workers), sort_keys=True))
    except store.StoreError as error:
        print(f"RESOLVE FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
