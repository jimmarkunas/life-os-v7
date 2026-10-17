"""Read-only diagnostic: which alerts are open and why. Prints open alert keys (source names only), per source the new jobs in the last 24 hours against the 14-day daily baseline,
the jobs the stuck_jobs alert counts, grouped by status, whether a link and a Notion page exist, the Fit admission and the stop reason code, and the shape of the page body publish would send for the admitted jobs that have no page (lengths and character counts). Counts only. Writes nothing.
With ALERTS_MESSAGE_REF (the SHA-256 of one Gmail message id, never the id itself) it returns only that message's trace instead: see trace()."""
import datetime as dt
import os
import re
import unicodedata

from lifeos.jobs import alerts, funnel, store

IN_FLIGHT, TERMINAL = ("NEW", "RESOLVED", "READY"), ("PUBLISHED", "CLOSED", "DUPLICATE", "PURGED")          # the newsletter finalize stage's sets (jobs cannot import sources; a test holds them equal)
MESSAGE_REF = re.compile(r"[0-9a-f]{64}")
KNOWN_STATUSES = frozenset(IN_FLIGHT + TERMINAL + ("HOLD", "EXCLUDED_FIT", "EXCLUDED_STALE", "EXCLUDED_UNRESOLVED"))
SOURCES = frozenset("dice jobright lensa linkedin-alerts reed".split())                  # the newsletter rules; anything else prints as "other"
REASONS = frozenset(("none http_N link_mismatch jd_thin jd_template jd_listing description_empty description_empty_tf hold_expired unresolved_after_recovery lane_exclude same_opening in_ledger repost "
                     "repost_ambiguous recovery_retry screen_pay listing_url no_job_id dup_other_source dup_of_final_url closed unknown Fit_N_below_N audit_url_listing_url audit_url_root audit_url_no_job_id "
                     "audit_url_workable_no_account audit_jd_template audit_jd_listing audit_jd_thin no_match_no_board+no_result no_match_no_title+no_result no_match_ambiguous+no_result").split())


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


def _select(cursor, sql, args):
    """The only way trace() touches the database: a SELECT, refused before it reaches the driver otherwise."""
    if not sql.lstrip().upper().startswith("SELECT"):
        raise store.StoreError("ALERT_STATE_NOT_READ_ONLY")
    cursor.execute(sql, args)
    return cursor.fetchall()


def trace(message_ref, now=None):
    """One message's vacancies in saved order, read-only and counts only: status, reason code, whether a Ledger page and a verification are recorded, and the outcome finalize would give each
    (a source row with no job is "unlinked", never absent). Totals: associations, terminal, blockers. A repeat link belongs to the message that first carried it."""
    if not MESSAGE_REF.fullmatch(message_ref):
        raise store.StoreError("ALERT_STATE_MESSAGE_REF_INVALID")
    try:
        with store.connect() as connection, connection.cursor() as cursor:
            rows = _select(cursor, "SELECT s.source, j.id IS NOT NULL, j.status, j.unresolved_reason, j.notion_page_id IS NOT NULL, j.verified_at IS NOT NULL FROM v7_job_sources s"
                           " LEFT JOIN v7_jobs j ON j.id = s.job_id WHERE SHA2(s.gmail_message_id, 256) = %s ORDER BY s.id", (message_ref,))     # the id is matched inside the query, never held or printed
    except store.StoreError:
        raise
    except Exception:
        raise store.StoreError("ALERT_STATE_QUERY_FAILED") from None
    vacancies, blockers = [], {}
    for n, (source, linked, status, reason, page, verified) in enumerate(rows, 1):
        status = status if isinstance(status, str) else None
        why = ("unlinked" if not linked else "malformed" if status is None else "in_flight" if status in IN_FLIGHT else "hold" if status == "HOLD"
               else "unverified" if status == "PUBLISHED" and not verified else None if status in TERMINAL or status.startswith("EXCLUDED_") else "other")      # finalize.blockers' rule
        if why:
            blockers[why] = blockers.get(why, 0) + 1
        code = funnel.reason_code(reason if isinstance(reason, str) else None).replace(" ", "_")
        vacancies.append({"n": n, "source": source if source in SOURCES else "other", "status": status if status in KNOWN_STATUSES else "other" if status else "none", "reason": code if code in REASONS else "other",
                          "outcome": why or "terminal", "ledger_page": bool(page), "verified": bool(verified)})
    return {"observed_at": (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "message_trace": {"associations": len(rows), "vacancies": vacancies, "terminal": len(rows) - sum(blockers.values()), "blockers": dict(sorted(blockers.items()))}}


def run(limit, live):
    message_ref = (os.environ.get("ALERTS_MESSAGE_REF") or "").strip()
    if message_ref:
        return trace(message_ref)
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
