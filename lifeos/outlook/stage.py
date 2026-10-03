"""Outlook stages. `auth` signs one mailbox in once (device code; the token goes straight into the private database, never into
a secret or a log). `probe` is a read-only health check that prints counts only. Imports only the platform."""
import os
import time
from datetime import datetime, timedelta, timezone

from lifeos.platform.outlook import Outlook, OutlookError, device_start, device_wait
from . import store

LABELS = ("personal", "work")


def _label(environ):
    label = (environ.get("OUTLOOK_ACCOUNT") or "").strip()
    if label not in LABELS:
        raise OutlookError("OUTLOOK_CONFIG_INVALID")
    return label


def _client_id(environ):
    value = (environ.get("OUTLOOK_CLIENT_ID") or "").strip()
    if not value:
        raise OutlookError("OUTLOOK_CONFIG_MISSING:OUTLOOK_CLIENT_ID")
    return value


def _connect():
    from lifeos.platform import db
    return db.connect()


def auth(limit, live, environ=os.environ, connect=None, sleep=time.sleep, clock=time.monotonic, say=lambda line: print(line, flush=True)):
    """Needs `live`: signing in is the one thing that must be saved, so a dry run only checks the configuration."""
    label, client_id = _label(environ), _client_id(environ)
    if not live:
        return {"account": 1, "signed_in": 0}
    started = device_start(client_id)
    # The sign-in code is single-use, short-lived and useless without the account's own password; it is the one thing printed.
    say(f"OUTLOOK SIGN-IN: open {started.get('verification_uri')} and enter code {started.get('user_code')}")
    reply = device_wait(client_id, started, sleep=sleep, clock=clock)
    with (connect or _connect)() as connection:
        store.ensure_schema(connection)
        store.save(connection, label, reply["refresh_token"])
    return {"account": 1, "signed_in": 1}


def probe(limit, live, environ=os.environ, connect=None, client_factory=None, now=None):
    """Counts only: for each signed-in account, how many inbox messages arrived in the last 7 days and how many are unread."""
    client_id = _client_id(environ)
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {"accounts": 0, "ok": 0, "failed": 0, "recent": 0, "unread": 0, "why": {}}
    with (connect or _connect)() as connection:
        store.ensure_schema(connection)
        labels = store.accounts(connection)
        out["accounts"] = len(labels)
        for position, label in enumerate(labels, 1):
            token = store.load(connection, label)
            try:
                client = (client_factory or Outlook)(client_id, token, lambda new, label=label: store.save(connection, label, new))
                messages = client.messages("inbox", since)
            except OutlookError as error:
                out["failed"] += 1
                code = f"{error}@{position}"                         # position, never the label or address
                out["why"][code] = out["why"].get(code, 0) + 1
                continue
            out["ok"] += 1
            out["recent"] += len(messages)
            out["unread"] += sum(1 for m in messages if not m.get("isRead"))
    if out["failed"] or not out["accounts"]:
        raise OutlookError(f"OUTLOOK_PROBE_FAILED:{out['failed']}of{out['accounts']}:" + ",".join(sorted(out["why"])))
    return out
