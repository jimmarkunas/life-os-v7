"""The System card stage (LIFE-975 step 4d; the stage keeps its old name `oracle-card`, so no registry or workflow moves): one writer for the Daily Report's System table.

It merges what three cards showed: Oracle's `oracle1` stats (the old Oracle Cloud card), Oracle's `limits` (the old AI Capacity card; Codex is read ONCE here, there is no second reading to disagree with) and the System
health database behind the Connections row (lifeos/platform/connections.py). It lives in sources because only sources may import the jobs layer (the `limits` reader), the oraclecloud layer (the card) and the platform.
A reading that cannot be had is never zero: its row keeps the page's last values and says `STALE since ...`. The AI Capacity callout and the Command Center source-health toggle have no writer any more. Counts and fixed codes only."""
import json
import os
from datetime import datetime, timezone

from lifeos.inbox import concord_reader
from lifeos.inbox.model import InboxError
from lifeos.jobs.fit import edge
from lifeos.oraclecloud import backup_watch, boot_backup_watch, card, system_table, worker_watch
from lifeos.platform import connections, ct_stamp, router
from lifeos.platform.fixed_code import fixed
from lifeos.platform.notion_client import NotionError
from lifeos.sources.cards import signin_reminder


def limits_of(host):
    """-> (Oracle's `limits` reading, the time it was read) or (None, None): unreachable or off contract."""
    try:
        reading = edge.limits(host)
    except edge.EdgeError as error:
        print("oracle-card: limits unavailable", json.dumps({"code": str(error)}))
        return None, None
    try:
        at = datetime.fromisoformat(reading["read_at"].replace("Z", "+00:00"))
    except (ValueError, KeyError, AttributeError):
        at = None
    return reading, at


def concord_of(host, fetcher=None):
    """-> the parsed `concord1` reading (a count and a fixed code, never message content). One ask per tick; a refusal or an unreachable endpoint is a failed read, not an error."""
    fetch = fetcher or (lambda h, request: card.fetch(h, request=request))
    try:
        got = concord_reader.health(host, fetch)
    except InboxError as error:
        got = {"ok": False, "code": str(error), "messages": 0, "at": 0}
    print("oracle-card: concord", json.dumps({"ok": got["ok"], "code": got["code"], "messages": got["messages"]}))
    return got


def connection_summary(client, environ, now, live, seen=None, concord=None):
    """-> (summary or None, {"changed", "retired"}). The owned rows are refreshed from the domains job history; a database that cannot be read is None (the row goes stale, the card still writes)."""
    try:
        rows = connections.read_rows(client, connections.source_id(environ))
    except NotionError:
        return None, {"changed": 0, "retired": 0}
    updates = connections.plan(rows, connections.evidence(environ) if seen is None else seen, now, concord)
    retire = connections.retired_pages(rows)
    if live:
        try:
            connections.apply(client, updates, retire)
        except NotionError:
            print("oracle-card: connections not written", json.dumps({"code": "CONNECTIONS_WRITE_FAILED"}))
            updates = []
    return connections.summary(connections.merged(rows, updates), now), {"changed": len(updates), "retired": len(retire)}


def reminders(reading, environ, now, send=None):
    """LIFE-993: the Agent Inbox renewal items for a sign-in due within three days (see signin_reminder). -> a count; never raises and never changes the card."""
    try:
        items = signin_reminder.plan((reading or {}).get("signins"), now)
        return signin_reminder.send_all(items, environ, send)["sent"] if items else 0
    except Exception:                                                                       # noqa: BLE001
        return 0


def run(limit, live, environ=os.environ, now=None, fetcher=card.fetch, client=None, limits=limits_of, seen=None, watch=None, workers_watch=None, boot_watch=None, concord_fetch=None, reminder_send=None):
    host = (environ.get("IMESSAGE_ALERTS_HOST") or "").strip()
    anchor = (environ.get("JIRA_CARD_BLOCK_ID") or "").strip()                              # LIFE-969: any card block on the page finds the page; the Jira card is on it
    if not host or not anchor:
        raise card._fail("ORACLE_NOT_CONFIGURED")
    router.check_write(card.MODULE, [card.TITLE])
    now = now or datetime.now(timezone.utc)
    client = client or card._writer(environ)
    heading, _, rows = card.locate(client, anchor)
    stats = card.stats_of(host, fetcher, now)
    reading, read_at = limits(host)
    concord = concord_of(host, concord_fetch)
    summary, written = connection_summary(client, environ, now, live, seen, concord)
    built = system_table.readings(stats, now if stats else None, reading, read_at or (now if reading else None), summary, now)
    stamp = connections.last_ok_time(card.heading_text_of(heading), now)
    table = system_table.finish(built, system_table.previous_of(card.table_of(rows)), stamp, now)
    heading_text = f"{card.TITLE} ({ct_stamp.when(now)})"
    counts = {"rows": len(table) - 1, "stale": system_table.stale_count(table), "oracle": int(stats is not None), "limits": int(reading is not None),
              "connections": f"{summary['connected']}/{summary['total']}" if summary else "unread", "concord": "ok" if concord["ok"] else concord["code"], "connections_written": written["changed"], "todoist_row_retired": written["retired"], "written": 0}
    if not live:
        return {**counts, **card.write(client, heading, rows, table, heading_text, False)}
    if stats is not None:
        (watch or backup_watch.record)(stats["backup"], environ, now)                       # LIFE-129: never raises; the card's own result never depends on it
        (workers_watch or worker_watch.record)(stats["workers"], environ, now)               # LIFE-531: same
        (boot_watch or boot_backup_watch.record)(stats["backups"], environ, now)             # LIFE-808: same
    counts["signin_reminders"] = reminders(reading, environ, now, reminder_send)
    scoped = card.scoped(environ, client, heading, rows)                                    # LIFE-613: only the heading and the table rows located above can be written
    with fixed(NotionError, card._fail, "ORACLE_WRITE_FAILED"):
        result = card.write(scoped, heading, rows, table, heading_text, True)
    return {**counts, **result, "written": result["rows_written"] + int(result["heading_written"])}
