"""MegIBOW stage (D134): Gmail + Outlook sent mail, Google + Outlook calendars, Jim's review decisions -> the 8-week table, warnings and the review list in Notion.

Flow: read every source (a failed source makes the run DEGRADED and the table is left as it was) -> apply Jim's review decisions -> classify (pure) -> project -> warnings -> render
-> write only the bounded block. Counts and fixed codes only in the log; names and addresses stay in memory and in Jim's private Notion pages.
Lives under sources because it may read the Jobs tables for the companies Jim is pursuing; the engine itself (lifeos/megibow) imports platform only."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import collections
import contextlib
import os

from lifeos.megibow import card, classify as C, evidence, project as P, render as R, review, store, warnings as W
from lifeos.megibow.windows import CHI, chicago, monday, window_utc
from lifeos.platform.gcal import GcalError, GoogleCalendar
from lifeos.platform.gmail import Gmail, GmailError
from lifeos.platform.notion_client import Client, NotionError
from lifeos.platform.outlook import OutlookError

FORWARD_DAYS = 60
DASHBOARD_PAGE = "3cf3c5a0-5926-8008-acfa-c0b4765caa92"        # Jim & Matt Dashboard V2 (an id, not a credential); the block is found by its shape
REVIEW_SOURCE = "23953290-4ca5-4d94-802c-656aae0ddfc7"        # the MegIBOW Review data source
CUTOVER_DEFAULT = "2026-10-05"


class MegibowError(NotionError):
    """Fixed codes only."""


def _writer(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def _outlook_clients(environ):
    from lifeos.attention import stage as attention                      # noqa: PLC0415 - the same sign-in loader Attention uses (sources may reach any OS)
    return attention._outlook_clients(environ)


def _meta(outcome, msgs, evs, ctx):
    """The private details of a review item (who, when, which people to remember): memory and Notion only."""
    key = outcome["key"]
    if key in msgs:
        m = msgs[key]
        people = C.external(list(m["to"]) + list(m["cc"]), ctx)
        when = chicago(m["sent_at"]).date()
    else:
        e = evs.get(key)
        people = C.external([a["email"] for a in (e or {}).get("attendees") or []], ctx)
        when = chicago((e or {})["start"]).date() if e else outcome["week"]
    return {"who": people[0] if people else "unknown", "people": [C.key_hash("contact", p) for p in people], "when": when}


def run(limit, live, environ=os.environ, gmail=None, gcal=None, outlook_clients=None, notion=None, connection=None, now=None, known=None):
    now = (now or datetime.now(timezone.utc)).astimezone(CHI)
    today = now.date()
    counts = {"gmail_sent": 0, "outlook_sent": 0, "events": 0, "sources_failed": 0, "degraded": False, "status": {}, "review_open": 0, "decisions_applied": 0, "frozen": 0,
              "warnings": 0, "rows_written": 0, "text_written": False, "why": []}
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
    accounts = list(outlook_clients if outlook_clients is not None else _outlook_clients(environ))
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
            from lifeos.platform import db                                    # noqa: PLC0415
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
    rows = []
    if review_id and notion is not None and environ.get("MEGIBOW_REVIEW_DB_ID", "on") != "off":
        review.check_schema(notion, review_id)
        rows = review.read(notion, review_id)
    sent_to = {}
    for m in msgs:
        for a in list(m["to"]) + list(m["cc"]):
            sent_to.setdefault(a, []).append(m["sent_at"])
    ctx = {"self": {a for a in own if a}, "known": known, "excluded": excluded, "overrides": dict(decisions), "contacts": dict(contacts), "sent_to": sent_to}
    by_msg = {C.key_hash("msg", m["id"]): m for m in msgs}
    by_ev = {}
    for e in evs:
        by_ev[C.key_hash("sched", e["uid"])] = e
        by_ev[C.key_hash("call", e["occ"])] = e
    # Jim's answers from the review list, applied before the recount (and remembered when live)
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
    cutover = date.fromisoformat((environ.get("MEGIBOW_CUTOVER") or CUTOVER_DEFAULT).strip())
    # Monday rollover: freeze every finished week from the cut-over on, once, with read-back; only from a complete read
    this_monday = monday(today)
    counts["buckets"] = dict(collections.Counter(f"{o['status']}:{o['reason']}" for o in outcomes))
    proj = P.project(outcomes, today, frozen, legacy, cutover)
    proj["review"] = [o for o in proj["review"] if o["week"] >= cutover]
    if live and connection is not None and not degraded:
        for week in proj["weeks"]:
            if week < this_monday and week >= cutover and week not in frozen and proj["counts"][week] is not None:
                clean = not any(o["week"] == week for o in proj["review"])
                store.freeze(connection, week, proj["counts"][week], clean)
                frozen[week], trusted[week] = dict(proj["counts"][week]), clean
                counts["frozen"] += 1
        proj = P.project(outcomes, today, frozen, legacy, cutover)
        proj["review"] = [o for o in proj["review"] if o["week"] >= cutover]
    baseline = []
    for week in proj["weeks"][:-1]:
        c = proj["counts"][week]
        if c is not None and (trusted.get(week) if week in frozen else not any(o["week"] == week for o in proj["review"])):
            baseline.append(c)
    forward = len({o["key"] for o in outcomes if o["status"] == C.COUNTED and o["activity"] == C.SCHEDULED and by_ev.get(o["key"]) and by_ev[o["key"]]["start"] >= now
                   and by_ev[o["key"]]["start"] <= now + timedelta(days=14)})
    unresolved = sum(1 for o in proj["review"] if o["week"] == proj["current"])
    warns = W.evaluate(proj["counts"][proj["current"]], baseline, now, forward, degraded, unresolved)
    counts["warnings"], counts["review_open"] = len(warns), len(proj["review"])
    counts["status"] = {a: proj["counts"][proj["current"]][a] for a in C.ACTIVITIES}
    # the review list: add the uncertain items, mark answered ones Applied, supersede the rest
    items = [{"key": o["key"], "candidate": o["candidate"] or "Outreach", "why": o["reason"], "week": (o["meta"]["when"]).isoformat(), "who": o["meta"]["who"]} for o in proj["review"]]
    if review_id and notion is not None:
        wanted = review.plan(items, rows, now)
        if live and not degraded:
            review.write(notion, review_id, wanted)
        counts["review_created"] = len(wanted["create"])
    status = R.status(now, degraded, len(proj["review"]), None)
    text = status + (("\n" + R.notes(warns, degraded)) if R.notes(warns, degraded) else "")
    table = None if degraded else R.table(proj)
    block_id = (environ.get("MEGIBOW_BLOCK_ID") or "").strip() or card.find(notion, (environ.get("MEGIBOW_PAGE_ID") or DASHBOARD_PAGE).strip())
    counts.update(card.write(notion, block_id, text, table, live))
    if degraded:
        _alert(environ, now, live, counts)
        raise MegibowError("MEGIBOW_DEGRADED:" + ";".join(counts["why"]))
    return counts


def known_companies(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT DISTINCT company FROM v7_jobs WHERE status IN ('PUBLISHED','READY') AND company IS NOT NULL")
        rows = cursor.fetchall()
    return {n for n in (C.normal(r[0]) for r in rows) if len(n) >= 3}


def _alert(environ, now, live, counts):
    """Wednesday 1:30 PM and 2:30 PM CT: a refresh that is still failing is worth a phone push (counts only); the table itself says DEGRADED regardless."""
    if not live or now.weekday() != 2 or not ((now.hour, now.minute) >= (13, 30) and now.hour < 15):
        return
    from lifeos.platform import alerts                                    # noqa: PLC0415
    body = f"{counts['sources_failed']} source(s) unread; the table shows DEGRADED"
    counts["notified"] = alerts.ntfy((environ.get("NTFY_TOPIC") or "").strip(), "MegIBOW is not fresh for the 3 PM meeting", body, alerts.PAGE)
