"""Load the UK Home Office register of licensed sponsors (workers) into Hostinger, Skilled Worker route only.

The gov.uk content API names the current CSV attachment; the CSV is read by header name (never by position), kept for the Skilled Worker
route and A-rated licences, and replaces v7_sponsors in one transaction. Anything unexpected (HTTP error, no CSV link, missing columns, or
suspiciously few rows) is FAILED and the previous register stays. Refreshed at most weekly unless SPONSORS_FORCE=true. Counts only.
"""
import csv
from datetime import datetime, timedelta, timezone
import io
import json
import os
import re

from lifeos.jobs import names, store
from lifeos.platform.http import fetch

PAGE = "https://www.gov.uk/api/content/government/publications/register-of-licensed-sponsors-workers"
CSV_URL = re.compile(r"https://assets\.publishing\.service\.gov\.uk/[^\"\s\\]+\.csv", re.I)
MIN_ROWS = 5000                       # the register holds well over 100,000 licences; a short file is a bad download
REFRESH_DAYS = 7
MAX_BYTES = 80_000_000


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def csv_url(fetcher=fetch):
    page = fetcher(PAGE, timeout=30, max_hops=2, max_bytes=3_000_000)
    if page.status != 200:
        return None, f"http_{page.status}" if page.status else "network"
    try:
        json.loads(page.html)
    except ValueError:
        return None, "bad_json"
    found = CSV_URL.findall(page.html)
    return (found[0], None) if found else (None, "no_csv_link")


def parse(text):
    """-> (names, reason). Skilled Worker route, A-rated (when the rating column exists), unique by distinctive tokens."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}
    name_col, route_col = fields.get("organisation name"), fields.get("route")
    if not name_col or not route_col:
        return [], "bad_columns"
    rating_col = next((f for k, f in fields.items() if "rating" in k), None)
    found = {}
    for row in reader:
        if (row.get(route_col) or "").strip().lower() != "skilled worker":
            continue
        if rating_col and "a rating" not in (row.get(rating_col) or "").lower() and "a (premium)" not in (row.get(rating_col) or "").lower():
            continue
        name = (row.get(name_col) or "").strip()
        key = tuple(names.core(name))
        if key:
            found.setdefault(key, name[:200])
    return list(found.values()), None


def run(limit, live, now=None, fetcher=fetch, environ=os.environ):
    now = now or _now()
    counts = {"status": "OK", "sponsors": 0, "saved": bool(live)}
    with store.connect() as connection:
        store.ensure_schema(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT MAX(refreshed_at) FROM v7_sponsors")
            latest = cursor.fetchone()[0]
    if latest and now - latest < timedelta(days=REFRESH_DAYS) and environ.get("SPONSORS_FORCE") != "true":
        counts["status"] = "FRESH"
        return counts
    url, why = csv_url(fetcher)
    if not url:
        return {**counts, "status": f"FAILED:{why}"}
    page = fetcher(url, timeout=120, max_hops=3, max_bytes=MAX_BYTES)
    if page.status != 200:
        return {**counts, "status": f"FAILED:http_{page.status}" if page.status else "FAILED:network"}
    entries, why = parse(page.html)
    if why or len(entries) < MIN_ROWS:
        return {**counts, "status": f"FAILED:{why or 'too_few'}", "sponsors": len(entries)}
    counts["sponsors"] = len(entries)
    if live:
        with store.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM v7_sponsors")
                for start in range(0, len(entries), 1000):
                    cursor.executemany("INSERT IGNORE INTO v7_sponsors (name_key, name, refreshed_at) VALUES (%s,%s,%s)",
                                       [(" ".join(names.core(n))[:160], n, now) for n in entries[start:start + 1000]])
    return counts
