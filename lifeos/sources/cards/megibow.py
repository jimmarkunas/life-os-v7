"""MegIBOW stage: sent mail, calendars and review decisions -> the 8-week table, warnings and review list in Notion.

Flow: read every source (a failed source makes the run DEGRADED and leaves the table as it was) -> apply review
decisions -> classify (pure) -> project -> warnings -> render -> write only the bounded block. Counts and fixed
codes only in the log. Lives under sources because it may read the Jobs tables; lifeos/megibow imports platform
only.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import collections
import contextlib
import json
import os

from lifeos.platform.notion_ids import notion_id
from lifeos.megibow import (
    card,
    classify as C,
    evidence,
    history,
    project as P,
    render as R,
    review,
    store,
    v3,
    warnings as W,
    weekly,
)
from lifeos.megibow.windows import CHI, chicago, monday, window_utc
from lifeos.platform.gcal import GcalError, GoogleCalendar
from lifeos.platform import ct_stamp, mail
from lifeos.platform.gmail import Gmail, GmailError
from lifeos.platform.notion_client import Client, NotionError
from lifeos.platform.outlook import OutlookError

FORWARD_DAYS = 60
DASHBOARD_PAGE = notion_id("DASHBOARD_PAGE")
REVIEW_SOURCE = notion_id("REVIEW_SOURCE")
CUTOVER_DEFAULT = "2026-10-05"


class MegibowError(NotionError):
    """Fixed codes only."""


def _writer(environ):
    return Client(
        {
            "NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(),
            "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused",
        }
    )


def _meta(outcome, msgs, evs, ctx):
    key = outcome["key"]
    if key in msgs:
        m = msgs[key]
        people = C.external(list(m["to"]) + list(m["cc"]), ctx)
        when = chicago(m["sent_at"]).date()
    else:
        e = evs.get(key)
        people = C.external([a["email"] for a in (e or {}).get("attendees") or []], ctx)
        when = chicago((e or {})["start"]).date() if e else outcome["week"]
    return {
        "who": people[0] if people else "unknown",
        "people": [C.key_hash("contact", p) for p in people],
        "when": when,
    }


def run(
    limit,
    live,
    environ=os.environ,
    gmail=None,
    gcal=None,
    outlook_clients=None,
    notion=None,
    connection=None,
    now=None,
    known=None,
):
    """Jim's table first, then Matt's, each isolated: a failure of either never changes the other. Jim's failure is
    raised after Matt's write; Matt's alone is `failed` 1 (MEGIBOW_MATT_DEGRADED).
    """
    now = (now or datetime.now(timezone.utc)).astimezone(CHI)
    jim_error = counts = None
    try:
        counts = _jim(limit, live, environ, gmail, gcal, outlook_clients, notion, connection, now, known)
    except Exception as error:  # noqa: BLE001 - re-raised below, after Matt's own write
        jim_error = error
    try:
        notion = notion or _writer(environ)
    except NotionError:
        notion = None
    matt = _matt(notion, now, live)
    if jim_error is not None:
        print("megibow-matt:", json.dumps(matt, sort_keys=True))
        raise jim_error
    counts["matt"] = matt
    if matt["status"] != "OK":
        counts["failed"] = 1
    return counts


def _matt(notion, now, live):
    """Matt's callout from his own database. Never raises: -> {status OK | DEGRADED, code, entered, warnings,
    rows_written, ...}.
    """
    out = {"status": "DEGRADED", "code": "", "entered": 0, "warnings": 0, "rows_written": 0, "ignored": 0}
    when = ct_stamp.when(now)
    try:
        if notion is None:
            raise v3.V3Error("MEGIBOW_V3_TARGET_NOT_FOUND")
        block_id = v3.matt_block(notion)
        try:
            entered, out["ignored"] = v3.matt_rows(notion, v3.find(notion, v3.MATT_SOURCE_TITLE, "data_source"))
        except Exception as error:  # noqa: BLE001 - an unreadable database keeps the last accepted rows
            out["code"] = _code(error)
            out.update(
                v3.write(
                    notion,
                    block_id,
                    f"DEGRADED at {ct_stamp.when(now)} — Matt's database could not be read; showing the last accepted rows",
                    None,
                    None,
                    live,
                )
            )
            out.pop("summary_page", None)
            return out
        proj = v3.matt_project(entered, now.date())
        warns, suppressed = v3.matt_warnings(proj, now)
        lines = warns or (
            [f"Warnings start once {v3.MIN_ENTERED} weeks are entered ({proj['entered']} so far)."]
            if suppressed
            else [W.HEALTHY]
        )
        stamp = f"Last refreshed {when}"
        link = v3.history_link(notion, "Matt")
        notes = [("\n".join(lines) + "\n", None), ("Megibow history", link)] if link else [("\n".join(lines), None)]
        out.update(
            v3.write(notion, block_id, stamp, v3.table(proj["weeks"], proj["counts"], proj["cumulative"]), notes, live)
        )
        out["summary_written"] = v3.write_summary(
            notion,
            out.pop("summary_page"),
            v3.summary("Matt", stamp, proj["weeks"], proj["counts"], proj["cumulative"], lines),
            live,
        )
        out.update(status="OK", entered=proj["entered"], warnings=len(warns))
    except Exception as error:  # noqa: BLE001 - Matt's failure never reaches Jim's result
        out["code"] = out["code"] or _code(error)
    out.pop("summary_page", None)
    return out


def _code(error):
    text = str(error)
    return text if text.replace("_", "").isalnum() and text.isupper() else type(error).__name__


def _jim(limit, live, environ, gmail, gcal, outlook_clients, notion, connection, now, known):
    today = now.date()
    counts = {
        "gmail_sent": 0,
        "outlook_sent": 0,
        "events": 0,
        "sources_failed": 0,
        "degraded": False,
        "status": {},
        "review_open": 0,
        "decisions_applied": 0,
        "frozen": 0,
        "warnings": 0,
        "rows_written": 0,
        "text_written": False,
        "why": [],
    }
    start, end = window_utc(today)
    far = now.astimezone(timezone.utc) + timedelta(days=FORWARD_DAYS)
    msgs, evs, own, failed = [], [], set(), 0

    def fail(label, error):
        nonlocal failed
        failed += 1
        counts["why"].append(f"{label}:{str(error)[:90]}")

    try:
        gmail = gmail or Gmail.from_env()
        found, mine = evidence.gmail_sent(gmail, start, end)
        msgs += found
        own.add(mine)
        counts["gmail_sent"] = len(found)
    except (GmailError, KeyError) as error:
        fail("gmail", error)
    try:
        gcal = gcal or GoogleCalendar.from_env(environ)
        found, mine = evidence.gcal_events(gcal, start, far)
        evs += found
        own |= mine
    except GcalError as error:
        fail("gcal", error)
    accounts = list(
        outlook_clients
        if outlook_clients is not None
        else ((label, client, client is None) for label, client in mail.outlook_accounts(environ))
    )
    for account, client, connect_error in accounts:
        if connect_error:
            fail(f"outlook@{account}", "sign-in unreadable")
            continue
        try:
            sent, mine = evidence.outlook_sent(client, account, start, end)
            found, mine2 = evidence.outlook_events(client, start, far)
        except OutlookError as error:
            fail(f"outlook@{account}", error)
            continue
        msgs += sent
        evs += found
        own |= mine | mine2
        counts["outlook_sent"] += len(sent)
    counts["events"], counts["sources_failed"] = len(evs), failed
    degraded = failed > 0
    counts["degraded"] = degraded

    with contextlib.ExitStack() as stack:
        if connection is None and environ.get("LIFEOS_ACQ_DB_NAME"):
            from lifeos.platform import db  # noqa: PLC0415

            connection = stack.enter_context(db.connect())
        return _finish(counts, live, environ, notion, connection, now, today, msgs, evs, own, degraded, known)


def _finish(counts, live, environ, notion, connection, now, today, msgs, evs, own, degraded, known):
    frozen, legacy, decisions, contacts, trusted = ({}, None, {}, {}, {})
    if connection is not None:
        if live:
            store.ensure(connection)
        frozen, legacy, decisions, contacts, trusted = store.load(connection)
        if known is None:
            known = known_companies(connection)
    known = known or set()
    excluded = {d.strip().lower() for d in (environ.get("MEGIBOW_EXCLUDED_DOMAINS") or "").split(",") if d.strip()}
    review_id = (environ.get("MEGIBOW_REVIEW_DB_ID") or REVIEW_SOURCE).strip().replace("collection://", "")
    notion = notion or _writer(environ)
    try:  # the block ID register (row jim.megibow) is the address; MEGIBOW_BLOCK_ID is only the fallback
        block_id, counts["block_source"] = v3.registered(notion, "jim.megibow"), "register"
    except v3.V3Error as error:
        if str(error) != "MEGIBOW_V3_TARGET_NOT_FOUND":
            raise
        block_id, counts["block_source"] = (environ.get("MEGIBOW_BLOCK_ID") or "").strip(), "secret"
    if not block_id:
        raise card._fail("BLOCK_NOT_FOUND")
    layout_v3 = v3.is_v3(
        notion, block_id
    )  # the callout (heading first); the older block while the register has no jim.megibow row
    if legacy is None:
        legacy = card.read_legacy(notion, block_id)
        if legacy and live and connection is not None:
            store.save_legacy(connection, legacy)
            counts["legacy_seeded"] = True
    rows, review_db = [], ""
    if review_id and notion is not None and environ.get("MEGIBOW_REVIEW_DB_ID", "on") != "off":
        review_db = (review.check_schema(notion, review_id).get("parent") or {}).get("database_id") or ""
        rows = review.read(notion, review_id)
    sent_to = {}
    for m in msgs:
        for a in list(m["to"]) + list(m["cc"]):
            sent_to.setdefault(a, []).append(m["sent_at"])
    ctx = {
        "self": {a for a in own if a},
        "known": known,
        "excluded": excluded,
        "overrides": dict(decisions),
        "contacts": dict(contacts),
        "sent_to": sent_to,
    }
    by_msg = {C.key_hash("msg", m["id"]): m for m in msgs}
    by_ev = {}
    for e in evs:
        by_ev[C.key_hash("sched", e["uid"])] = e
        by_ev[C.key_hash("call", e["occ"])] = e
    plan = review.plan([], rows, now)
    answered = plan["decisions"]
    ctx["overrides"].update(answered)
    counts["decisions_applied"] = len(answered)
    outcomes = [C.message(m, ctx) for m in msgs]
    for e in evs:
        outcomes += C.event(e, ctx, now)
    for o in outcomes:
        o["meta"] = _meta(o, by_msg, by_ev, ctx) if o["status"] == C.REVIEW else None
    if live and connection is not None:
        for key, decision in answered.items():
            store.remember(connection, key, "ACTIVITY", decision)
            kind = store.activity_of(decision)
            meta = next((o["meta"] for o in outcomes if o["key"] == key and o["meta"]), None)
            if kind in C.CALLS and meta:
                for contact in meta["people"]:
                    store.remember(connection, contact, "CONTACT", kind)
    manual, bad_manual, has_line = {}, 0, True
    try:
        manual, bad_manual, has_line = card.read_manual(notion, block_id, today)
    except NotionError:
        pass
    counts["manual_unreadable"] = bad_manual
    if live and not has_line and not layout_v3:
        try:
            card.ensure_manual_line(notion, block_id)
            counts["manual_line_added"] = True
        except NotionError:
            pass
    cutover = date.fromisoformat((environ.get("MEGIBOW_CUTOVER") or CUTOVER_DEFAULT).strip())
    # Monday rollover: freeze every finished week from the cut-over on, once, with read-back, from a complete read
    # only from a complete read
    this_monday = monday(today)
    counts["buckets"] = dict(collections.Counter(f"{o['status']}:{o['reason']}" for o in outcomes))
    proj = P.project(outcomes, today, frozen, legacy, cutover, history.WEEKS, manual)
    proj["review"] = [o for o in proj["review"] if o["week"] >= cutover]
    if live and connection is not None and not degraded:
        for week in proj["weeks"]:
            if week < this_monday and week >= cutover and week not in frozen and proj["counts"][week] is not None:
                clean = not any(o["week"] == week for o in proj["review"])
                store.freeze(connection, week, proj["counts"][week], clean)
                frozen[week], trusted[week] = dict(proj["counts"][week]), clean
                counts["frozen"] += 1
        proj = P.project(outcomes, today, frozen, legacy, cutover, history.WEEKS, manual)
        proj["review"] = [o for o in proj["review"] if o["week"] >= cutover]
    baseline = []
    for week in proj["weeks"][:-1]:
        c = proj["counts"][week]
        if c is not None and (
            trusted.get(week) if week in frozen else not any(o["week"] == week for o in proj["review"])
        ):
            baseline.append(c)
    forward = len(
        {
            o["key"]
            for o in outcomes
            if o["status"] == C.COUNTED
            and o["activity"] == C.SCHEDULED
            and by_ev.get(o["key"])
            and by_ev[o["key"]]["start"] >= now
            and by_ev[o["key"]]["start"] <= now + timedelta(days=14)
        }
    )
    unresolved = sum(1 for o in proj["review"] if o["week"] == proj["current"])
    warns = W.evaluate(proj["counts"][proj["current"]], baseline, now, forward, degraded, unresolved)
    counts["warnings"], counts["review_open"] = len(warns), len(proj["review"])
    counts["status"] = {a: proj["counts"][proj["current"]][a] for a in C.ACTIVITIES}
    items = [
        {
            "key": o["key"],
            "candidate": o["candidate"] or "Outreach",
            "why": o["reason"],
            "week": (o["meta"]["when"]).isoformat(),
            "who": o["meta"]["who"],
        }
        for o in proj["review"]
    ]
    if review_id and notion is not None:
        wanted = review.plan(items, rows, now)
        if live and not degraded:
            review.write(notion, review_id, wanted)
        counts["review_created"] = len(wanted["create"])
    if layout_v3:
        shown, weekly_error = proj, ""
        try:
            source_id = v3.find(notion, weekly.JIM_SOURCE_TITLE, "data_source")
            rows, counts["weekly_ignored"] = weekly.read(notion, source_id)
            if not degraded:
                counts["weekly"] = weekly.close(notion, source_id, rows, proj, live)
                if counts["weekly"]["created"] or counts["weekly"]["finalized"]:
                    rows, counts["weekly_ignored"] = weekly.read(notion, source_id)
            shown = weekly.project(proj, rows)
        except v3.V3Error as error:
            if str(error) != "MEGIBOW_V3_TARGET_NOT_FOUND":
                raise
            counts["weekly"] = "MISSING"
        except NotionError as error:
            weekly_error = counts["weekly_code"] = _code(error)
        counts.update(_jim_v3(notion, block_id, shown, warns, degraded, review_db, now, live, weekly_error))
    else:
        status = R.status(now, degraded, len(proj["review"]), None)
        text = status + (("\n" + R.notes(warns, degraded)) if R.notes(warns, degraded) else "")
        table = None if degraded else R.table(proj)
        counts.update(card.write(notion, block_id, text, table, live))
    if degraded:
        _alert(environ, now, live, counts)
        raise MegibowError("MEGIBOW_DEGRADED:" + ";".join(counts["why"]))
    return counts


def _jim_v3(notion, block_id, proj, warns, degraded, review_db, now, live, weekly_error=""):
    when = ct_stamp.when(now)
    if weekly_error and not degraded:
        return v3.write(
            notion,
            block_id,
            f"DEGRADED at {ct_stamp.when(now)} — Jim's weekly database could not be read; showing the last accepted counts",
            None,
            None,
            live,
        ) | {"layout": "v3"}
    if degraded:
        stamp = f"DEGRADED at {ct_stamp.when(now)} — counts may be incomplete; showing the last accepted counts"
        return v3.write(notion, block_id, stamp, None, [(W.SOURCE_UNAVAILABLE, None)], live) | {"layout": "v3"}
    stamp, n = f"Last refreshed {when}", len(proj["review"])
    lines = (warns or [W.HEALTHY]) + ([v3.TYPED_NOTE] if any(proj.get("typed", {}).values()) else [])
    review_line = f"Review needed — {n} activity candidate{'s' if n != 1 else ''}" if n else "Review needed — none"
    link = f"https://www.notion.so/{review_db.replace('-', '')}" if review_db else None
    notes = [("\n".join(lines) + "\n", None), (review_line + " · ", None), ("MegIBOW Review", link)]
    history_url = v3.history_link(notion, "Jim")
    if history_url:
        notes += [(" · ", None), ("Megibow history", history_url)]
    out = v3.write(
        notion,
        block_id,
        stamp,
        v3.table(proj["weeks"], proj["counts"], proj["cumulative"], proj.get("typed")),
        notes,
        live,
    )
    out["summary_written"] = v3.write_summary(
        notion,
        out.pop("summary_page"),
        v3.summary("Jim", stamp, proj["weeks"], proj["counts"], proj["cumulative"], lines + [review_line]),
        live,
    )
    return out | {"layout": "v3"}


def known_companies(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT company FROM v7_jobs WHERE status IN ('PUBLISHED','READY') AND company IS NOT NULL"
        )
        rows = cursor.fetchall()
    return {n for n in (C.normal(r[0]) for r in rows) if len(n) >= 3}


def _alert(environ, now, live, counts):
    if not live or now.weekday() != 2 or not ((now.hour, now.minute) >= (13, 30) and now.hour < 15):
        return
    from lifeos.platform import alerts  # noqa: PLC0415

    body = f"{counts['sources_failed']} source(s) unread; the table shows DEGRADED"
    counts["notified"] = alerts.push(
        "megibow_stale", "MegIBOW is not fresh for the 3 PM meeting", body, (environ.get("NTFY_TOPIC") or "").strip()
    )
