"""LIFE-918: the `jira-reconcile-daily` stage of the hourly tick, off unless MEJ_SPRINT_AUTOMATION is on; JFM and MEJ as one unit.

On: once per Chicago date at or after 8:00 AM CT, the sprint inventory, recurring clones and closure counts; a dry run reads only. A live run writes only through lifeos.jira.writers (JIRA_WRITERS_VERIFIED yes).
State is one private row of v7_jira_rollover, alerts are one phone push per code per day, the Daily Report line is one summary row; `after` is the promotion hook. Counts and fixed codes only."""
# The Agent Inbox needs the inbox Notion token, which the Jira job deliberately does not hold (tests/jira/test_jira.py keeps NOTION_API_TOKEN out of it), so an alert is the existing phone push (INFO, deduped).
# The Daily Report summary row is saved beside the Jira snapshots under key `_recon`; the card reads it and never calls Jira. Alert rows `rc...` are keyed by a hash of the failure code.

import hashlib
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.platform import alerts, db
from lifeos.platform.jira import Jira, JiraError

from . import reconcile, recurrence, store, writers

KEY = "reconcile"
ALERT_KEY = "jira_reconcile_failed"
RECON_ROW = "_recon"
SCHEMA = ("""CREATE TABLE IF NOT EXISTS v7_jira_rollover (
        k VARCHAR(16) NOT NULL PRIMARY KEY, last_ok DATETIME NOT NULL, alert_open TINYINT NOT NULL DEFAULT 0, last_notified DATETIME NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",)
SUMMARY_SCHEMA = 1


class DbState:
    """reconcile.run's `state`: get() -> the last reconciled Chicago date or None, set(date)."""

    def __init__(self, connection):
        self.connection = connection
        with connection.cursor() as cursor:
            for statement in SCHEMA:
                cursor.execute(statement)

    def get(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT last_ok FROM v7_jira_rollover WHERE k=%s", (KEY,))
            row = cursor.fetchone()
        return row[0].date() if row else None

    def set(self, day):
        with self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO v7_jira_rollover (k, last_ok, alert_open, last_notified) VALUES (%s,%s,0,NULL) ON DUPLICATE KEY UPDATE last_ok=VALUES(last_ok)",
                           (KEY, datetime(day.year, day.month, day.day)))

    def notified(self, name, day, once=False):
        """True when `name` was already pushed today (or ever, with `once`); the row is keyed by a hash of the name, never the name."""
        row_key = "rc" + hashlib.sha1(name.encode()).hexdigest()[:12]
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT last_notified FROM v7_jira_rollover WHERE k=%s", (row_key,))
            row = cursor.fetchone()
        return bool(row and row[0] and (once or row[0].date() == day)), row_key

    def mark(self, row_key, now):
        with self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO v7_jira_rollover (k, last_ok, alert_open, last_notified) VALUES (%s,%s,1,%s) ON DUPLICATE KEY UPDATE last_notified=VALUES(last_notified), alert_open=1",
                           (row_key, now.replace(tzinfo=None), now.replace(tzinfo=None)))


def make_alert(state, environ, now, push=alerts.push):
    """-> alert(subject, details, key=None, once=False): one phone push per failure per Chicago day (once ever for a promotion failure), recorded only after the push was accepted."""
    day = now.astimezone(ZoneInfo(reconcile.TZ)).date()

    def alert(subject, details, key=ALERT_KEY, once=False):
        row = None
        try:
            seen, row = state.notified(f"{key}:{subject}:{details}", day, once)
            if seen:
                return False
            if push(key, subject, details, environ.get("NTFY_TOPIC", "")):
                state.mark(row, now)
                return True
        except Exception:                                                      # noqa: BLE001 - alerting never changes the stage's own result
            return False
        return False
    return alert


def save_summary(connection, now, line, ok):
    store.ensure_schema(connection)
    store._STORE.save(connection, RECON_ROW, {"schema": SUMMARY_SCHEMA, "taken_at": now.isoformat(), "line": line, "ok": ok})


def run(limit, live, environ=os.environ, client=None, now=None, connect=None, writers_=None, sleep=time.sleep, push=alerts.push, after=None):
    if not reconcile.enabled(environ):
        return {"enabled": 0}
    tz = ZoneInfo(reconcile.TZ)
    now = now or datetime.now(tz)
    client = client or Jira.from_env(environ)
    chosen = writers_ if writers_ is not None else writers.live_writers(client, environ)
    with (connect or db.connect)() as connection:
        state = DbState(connection)
        alert = make_alert(state, environ, now, push)

        def notify(subject, details):
            alert(subject, details)
        try:
            manual_force = str(environ.get("GITHUB_EVENT_NAME") or "") == "workflow_dispatch" and str(environ.get("JIRA_RECONCILE_FORCE") or "").strip().lower() == "true"
            total = reconcile.run(limit, live, environ=environ, client=client, now=now, writers=chosen, state=state, alert=notify, sleep=sleep, extra=recurrence.extra(environ), force=manual_force)
        except JiraError as error:
            if live:
                save_summary(connection, now, f"Jira sprints: DEGRADED · {str(error)[:120]}", False)
            raise
        if total.get("not_due"):
            return total
        line = recurrence.summary(total, live)
        total["summary"] = line
        if live:
            save_summary(connection, now, line, True)
        if after is not None:                                                  # lifeos.run passes the AC6 promotion call site (jira may not import accountability)
            total["promotion"] = after(environ, now, live, alert)
        return total
