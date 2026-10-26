"""LIFE-121 London coverage, step 1 (Jim, Oct 26: "Do them!"): read-only comparison of the CURRENT official sponsor register against V7's Scale-Up registry. Counts in the clear; the names of
London Scale-up-route sponsors V7 does not hold go to ONE private Notion page (lifeos.jobs.private_report), only when live; a dry run writes nothing."""
import csv
import io
import json
import pathlib

from lifeos.jobs import names, private_report
from lifeos.platform.http import fetch
from lifeos.sources import sponsor_register

REGISTRY = pathlib.Path(__file__).parent / "web" / "scale_up.json"


def rows_of(text):
    """-> ([(name, town, county)] on the Scale-up route, reason). By header name, never by position."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}
    name_col, route_col = fields.get("organisation name"), fields.get("route")
    if not name_col or not route_col:
        return [], "bad_columns"
    town_col, county_col = fields.get("town/city"), fields.get("county")
    out = {}
    for row in reader:
        if (row.get(route_col) or "").strip().lower() != "scale-up":
            continue
        name = (row.get(name_col) or "").strip()
        key = tuple(names.core(name))
        if key:
            out.setdefault(key, (name, (row.get(town_col) or "").strip() if town_col else "", (row.get(county_col) or "").strip() if county_col else ""))
    return list(out.values()), None


def held_keys(path=REGISTRY):
    return {tuple(names.core(s["company"])) for s in json.loads(path.read_text())["sources"]}


def compare(entries, held):
    """-> (counts, the sponsors in London that V7 does not hold). London = the register's Town/City names London."""
    london = [e for e in entries if "london" in e[1].lower()]
    missing = [e for e in london if tuple(names.core(e[0])) not in held]
    counts = {"register_scale_up": len(entries), "london": len(london), "london_held": len(london) - len(missing), "london_missing": len(missing),
              "held_total": len(held), "held_in_register": sum(1 for e in entries if tuple(names.core(e[0])) in held)}
    return counts, missing


def run(limit, live, fetcher=fetch, client=None):
    url, why = sponsor_register.csv_url(fetcher)
    if not url:
        return {"status": f"FAILED:{why}"}
    page = fetcher(url, timeout=120, max_hops=3, max_bytes=sponsor_register.MAX_BYTES)
    if page.status != 200:
        return {"status": f"FAILED:http_{page.status}" if page.status else "FAILED:network"}
    entries, why = rows_of(page.html)
    if why or len(entries) < 10:
        return {"status": f"FAILED:{why or 'too_few'}", "register_scale_up": len(entries)}
    counts, missing = compare(entries, held_keys())
    from lifeos.platform.notion_client import Client
    rows = [[" ".join(v.split()) for v in e] for e in sorted(missing)]
    report = private_report.publish(client or Client(), "LIFE-121: London Scale-up sponsors not in the registry", ["name", "town", "county"], rows, live)
    return {"status": "OK" if not report["failed"] else "FAILED", **counts, "page": report["page"], "failed": report["failed"], "why": report["why"]}
