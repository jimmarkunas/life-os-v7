"""The weekly Megibow database: one row per week, never deleted, the source of the lifetime Cumulative.

Shown number: the typed override if filled; else the stored number of a finished week; else the engine's reading. The
engine writes a finished week once (Source = Engine); the sheet week open at load (OPEN_AT_LOAD) is finalized once it
ends unless overridden.
"""

from datetime import date
from urllib.parse import quote

from lifeos.megibow import classify as C, project as P
from lifeos.megibow.windows import monday
from lifeos.platform import readback
from lifeos.platform.notion_client import NotionError, rich_text

JIM_SOURCE_TITLE = "Jim — MegIBOW weekly"
OVERRIDE = {
    "Outreach": "Outreach override",
    "Scheduled": "Scheduled override",
    "Networking Calls": "Networking override",
    "Recruiter Calls": "Recruiter override",
    "Company Calls": "Company override",
}
FIELDS = {
    "Week Start": "date",
    "Source": "select",
    **{a: "number" for a in C.ACTIVITIES},
    **{o: "number" for o in OVERRIDE.values()},
}
OPEN_AT_LOAD = date(2026, 11, 9)


class WeeklyError(NotionError):
    """Fixed codes only."""


def _number(prop):
    value = (prop or {}).get("number")
    if value is None:
        return None
    if value < 0 or value != int(value):
        raise ValueError
    return int(value)


def read(client, source_id):
    """-> ({monday: {stored, over: {activity: n or None}, source, id}}, rows ignored)."""
    meta = client.call("GET", f"/data_sources/{quote(source_id, safe='')}")
    props = (meta or {}).get("properties") or {}
    if any((props.get(n) or {}).get("type") != t for n, t in FIELDS.items()):
        raise WeeklyError("MEGIBOW_WEEKLY_SCHEMA")
    weeks, edited, ignored, cursor = {}, {}, 0, None
    for _ in range(20):
        page = client.query_data_source(source_id, {"page_size": 100, **({"start_cursor": cursor} if cursor else {})})
        for row in page.get("results") or []:
            p = row.get("properties") or {}
            try:
                week = monday(
                    date.fromisoformat((((p.get("Week Start") or {}).get("date") or {}).get("start") or "")[:10])
                )
                stored = {a: _number(p.get(a)) for a in C.ACTIVITIES}
                over = {a: _number(p.get(o)) for a, o in OVERRIDE.items()}
            except ValueError:
                ignored += 1
                continue
            stamp = row.get("last_edited_time") or ""
            if week in weeks:
                ignored += 1
                if stamp <= edited[week]:
                    continue
            weeks[week], edited[week] = (
                {
                    "stored": None
                    if all(n is None for n in stored.values())
                    else {a: n or 0 for a, n in stored.items()},
                    "over": over,
                    "source": ((p.get("Source") or {}).get("select") or {}).get("name") or "",
                    "id": row.get("id"),
                },
                stamp,
            )
        if not page.get("has_more"):
            return weeks, ignored
        cursor = page.get("next_cursor")
    raise WeeklyError("MEGIBOW_WEEKLY_READ_INCOMPLETE")


def shown(week, current, row, engine):
    over = (row or {}).get("over") or {}
    base = engine if week == current or row is None or row["stored"] is None else row["stored"]
    if base is None and all(n is None for n in over.values()):
        return None, frozenset()
    return (
        {a: over[a] if over.get(a) is not None else (base or {}).get(a, 0) for a in C.ACTIVITIES},
        frozenset(a for a in C.ACTIVITIES if over.get(a) is not None),
    )


def project(proj, rows):
    """The engine projection with weekly rows laid over it: shown counts, `typed` {monday: activities}, lifetime
    Cumulative."""
    counts, typed = {}, {}
    for w in proj["weeks"]:
        counts[w], typed[w] = shown(w, proj["current"], rows.get(w), proj["counts"][w])
    lifetime = P.zero()
    for w in set(rows) | set(proj["weeks"]):
        if w > proj["current"]:
            continue
        c = counts[w] if w in counts else shown(w, proj["current"], rows.get(w), None)[0]
        for a in C.ACTIVITIES:
            lifetime[a] += (c or {}).get(a, 0)
    return {**proj, "counts": counts, "cumulative": lifetime, "typed": typed}


def _props(week, counts, source):
    return {
        "Week": {"title": rich_text(f"Week of {week.strftime('%b')} {week.day}")},
        "Week Start": {"date": {"start": week.isoformat()}},
        "Source": {"select": {"name": source}},
        **{a: {"number": counts[a]} for a in C.ACTIVITIES},
    }


def close(client, source_id, rows, proj, live):
    """Write each finished visible week that has no row once (Source = Engine) and finalize the open-at-load week once
    it ends. -> {created, finalized}."""
    out = {"created": 0, "finalized": 0}
    if not live:
        return out
    wanted = {}
    for w in proj["weeks"]:
        engine, row = proj["counts"][w], rows.get(w)
        if w >= proj["current"] or engine is None:
            continue
        if row is None:
            client.call_once(
                "POST",
                "/pages",
                {
                    "parent": {"type": "data_source_id", "data_source_id": source_id},
                    "properties": _props(w, engine, "Engine"),
                },
            )
            out["created"] += 1
            wanted[w] = engine
        elif w == OPEN_AT_LOAD and row["source"] == "Sheet" and all(n is None for n in row["over"].values()):
            client.update_page_properties(
                row["id"],
                {**{a: {"number": engine[a]} for a in C.ACTIVITIES}, "Source": {"select": {"name": "Engine"}}},
            )
            out["finalized"] += 1
            wanted[w] = engine
    if wanted:
        ok, _ = readback.confirmed(
            lambda: read(client, source_id)[0],
            lambda after: all((after.get(w) or {}).get("stored") == c for w, c in wanted.items()),
        )
        if not ok:
            raise WeeklyError("MEGIBOW_WEEKLY_VERIFY_FAILED")
    return out
