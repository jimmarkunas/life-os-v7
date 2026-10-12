"""Read-only diagnostic: which alerts are open and why. Prints open alert keys (source names only), per source the new jobs in the last 24 hours against the 14-day daily baseline,
the jobs the stuck_jobs alert counts, grouped by status, whether a link and a Notion page exist, the Fit admission and the stop reason code, and the shape of the page body publish would send for the admitted jobs that have no page (lengths and character counts). Counts only. Writes nothing."""
import datetime as dt
import unicodedata

from lifeos.jobs import alerts, funnel, store


def stuck(cursor, now):
    """The rows stuck_jobs counts (READY or RESOLVED, untouched for over a day) as {status|link|page|admission|reason: [count, oldest_hours]}; no job, company or URL leaves the store."""
    cutoff = now - dt.timedelta(hours=alerts.QUIET_HOURS)
    cursor.execute("SELECT j.status, j.final_apply_url IS NOT NULL, j.notion_page_id IS NOT NULL, COALESCE(f.admission, 'none'), j.unresolved_reason, COUNT(*), MIN(j.updated_at)"
                   " FROM v7_jobs j LEFT JOIN v7_job_fit f ON f.job_id=j.id WHERE j.status IN ('READY','RESOLVED') AND j.updated_at < %s GROUP BY 1, 2, 3, 4, 5", (cutoff,))
    out = {}
    for status, link, page, admission, reason, count, oldest in cursor.fetchall():
        key = f"{status}|link={int(bool(link))}|page={int(bool(page))}|fit={admission}|{funnel.reason_code(reason)}"
        have = out.get(key, [0, 0])
        out[key] = [have[0] + int(count), max(have[1], int((now - oldest).total_seconds() // 3600))]
    return dict(sorted(out.items()))


def _units(text):
    return len(text.encode("utf-16-le")) // 2


def body_shape(blocks):
    """Counts only for the page body publish would send: per block the rich-text lengths in characters and in UTF-16 units (Notion's 2000 limit may count either), and the characters Notion can refuse
    (control, NUL, lone surrogate, private-use), plus whether a block is empty. No text leaves the store. Third block (children[2]) is reported on its own: that is the one a refusal names."""
    out = {"blocks": len(blocks), "over_2000_chars": 0, "over_2000_units": 0, "empty": 0, "control": 0, "nul": 0, "surrogate": 0, "private_use": 0, "max_chars": 0, "max_units": 0, "child2": None}
    for index, block in enumerate(blocks):
        kind = block["type"]
        items = block[kind]["rich_text"]
        texts = [item["text"]["content"] for item in items]
        chars, units = max(len(t) for t in texts), max(_units(t) for t in texts)
        joined = "".join(texts)
        control = sum(1 for c in joined if unicodedata.category(c) == "Cc" and c not in "\n\t\r" and c != "\x00")
        nul, surrogate = joined.count("\x00"), sum(1 for c in joined if 0xD800 <= ord(c) <= 0xDFFF)
        private = sum(1 for c in joined if unicodedata.category(c) == "Co")
        out["over_2000_chars"] += chars > 2000
        out["over_2000_units"] += units > 2000
        out["empty"] += not joined.strip()
        out["control"] += control
        out["nul"] += nul
        out["surrogate"] += surrogate
        out["private_use"] += private
        out["max_chars"], out["max_units"] = max(out["max_chars"], chars), max(out["max_units"], units)
        if index == 2:
            out["child2"] = {"type": kind, "items": len(items), "chars": chars, "units": units, "empty": not joined.strip(), "control": control, "nul": nul, "surrogate": surrogate, "private_use": private}
    return out


def publish_bodies(cursor):
    """The READY jobs the gate would admit that have no Notion page yet (at most 5, oldest first): the body publish builds for each, as body_shape counts."""
    from lifeos.jobs import publish                                                  # noqa: PLC0415
    cursor.execute("SELECT d.summary, d.responsibilities, d.requirements, d.qualifications, d.full_text FROM v7_jobs j JOIN v7_job_descriptions d ON d.job_id=j.id"
                   " LEFT JOIN v7_job_fit f ON f.job_id=j.id WHERE j.status='READY' AND j.notion_page_id IS NULL AND f.admission IN ('ADMIT', 'REVIEW') ORDER BY j.first_seen LIMIT 5")
    out = []
    for summary, resp, req, qual, full in cursor.fetchall():
        desc = {"summary": summary, "responsibilities": resp, "requirements": req, "qualifications": qual}
        if not any(desc.values()):
            desc["summary"] = (full or "")[:6000]                                    # the same fallback publish.run applies
        out.append(body_shape(publish.body_blocks("diag", desc)))
    return out


def run(limit, live):
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    with store.connect() as connection:
        open_alerts = alerts.load_state(connection)
        counts = alerts.gather(connection, now)
        window = now - dt.timedelta(hours=alerts.STOP_WINDOW_HOURS)
        start = window - dt.timedelta(days=alerts.STOP_BASELINE_DAYS)
        with connection.cursor() as cursor:
            cursor.execute("SELECT source, SUM(first_seen > %s), SUM(first_seen <= %s), MAX(first_seen) FROM v7_jobs WHERE source IS NOT NULL AND first_seen > %s GROUP BY source",
                           (window, window, start))
            sources = {name: {"last24h": int(recent or 0), "per_day": round(int(base or 0) / alerts.STOP_BASELINE_DAYS, 1), "latest": str(latest)}
                       for name, recent, base, latest in cursor.fetchall()}
            stuck_rows = stuck(cursor, now)
            bodies = publish_bodies(cursor)
    return {"open_alerts": {key: {"severity": value["severity"], "last_notified": str(value["last_notified"])} for key, value in sorted(open_alerts.items())},
            "published_24h": counts["published_24h"], "stuck_jobs": counts["stuck_jobs"], "sources_failing": counts["sources_failing"],
            "stuck": stuck_rows, "publish_bodies": bodies, "sources": dict(sorted(sources.items()))}
