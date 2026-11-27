"""Event-driven memory health alerts inside the Oracle MCP: no timer, no probe, no new store.

Two signals, each one deduplicated Agent Inbox item per incident through the existing send_to_jim delivery:
- Outage: OUTAGE_AFTER consecutive memory calls that end in memory_unavailable open one incident and send one alert;
  the next memory call that reaches Honcho closes it and updates the same item (recovered). Caller errors (invalid
  argument, sensitive text refused, ask limit) neither count nor reset.
- Unknown callers: requests from a principal the service does not know (an lm1 credential id that is not registered, an
  OAuth identity that is not authorized, an unknown client label) counted per caller fingerprint in a rolling hour;
  UNKNOWN_AFTER in one hour sends one alert for that caller and hour. Known clients asking for a tenant they lack
  (Bytalos) and grant revocation drills are gate denials of registered principals and never reach this counter.
Alerts carry counts, fixed codes, fingerprints and CT times only. Idle outages (no caller) are not detected.
Sending never blocks or fails a request: it runs on a daemon thread and a failed send is reported on stderr."""

import hashlib
import json
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
OUTAGE_AFTER = 3
UNKNOWN_AFTER = 3
WINDOW = 3600
OUTAGE_CODE = "memory_unavailable"
SENDER = "memory-monitor"


def fingerprint(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:8]


def _ct(epoch):
    return datetime.fromtimestamp(epoch, CENTRAL).strftime("%b %-d %Y, %-I:%M %p CT")


def _report(record):
    print(json.dumps(record, separators=(",", ":"), sort_keys=True), file=sys.stderr, flush=True)


def _background(send, item, report):
    def go():
        try:
            send(item)
            report({"event": "memory_alert", "key": item["key"], "outcome": "sent"})
        except Exception as error:  # noqa: BLE001  an alert that cannot be delivered never breaks a request
            report(
                {
                    "event": "memory_alert",
                    "key": item["key"],
                    "outcome": "send_failed",
                    "reason": getattr(error, "reason", type(error).__name__),
                }
            )

    threading.Thread(target=go, daemon=True).start()


class Monitor:
    """send(item) delivers one Agent Inbox item (the service's send_to_jim). clock -> epoch seconds. dispatch(send, item,
    report) runs the send; tests pass a synchronous one."""

    def __init__(self, send, clock=time.time, dispatch=_background, report=_report):
        self.send, self.clock, self.dispatch, self.report = send, clock, dispatch, report
        self.lock = threading.Lock()
        self.failures, self.first_failure, self.incident = 0, None, None
        self.unknown, self.alerted = {}, set()

    def _emit(self, item):
        self.dispatch(self.send, {"urgency": "Today", "workspace": "Personal", **item}, self.report)

    def memory_outcome(self, reason):
        """reason: None when the call reached Honcho and succeeded, else the fixed error code it ended with."""
        now = self.clock()
        with self.lock:
            if reason == OUTAGE_CODE:
                self.failures += 1
                self.first_failure = self.first_failure or now
                if self.failures >= OUTAGE_AFTER and self.incident is None:
                    self.incident = f"memory-unavailable-{int(self.first_failure)}"
                    item = {
                        "key": self.incident,
                        "subject": f"Shared memory unavailable: {self.failures} memory calls in a row failed",
                        "details": f"First failure {_ct(self.first_failure)}; code {OUTAGE_CODE}. Recall and store return an error until Honcho answers again. This item is updated when memory recovers.",
                    }
                    self._emit(item)
                return
            if reason is not None:
                return  # a caller error says nothing about Honcho
            if self.incident is not None:
                item = {
                    "key": self.incident,
                    "subject": "Shared memory recovered",
                    "urgency": "FYI",
                    "details": f"Failed from {_ct(self.first_failure)} ({self.failures} calls); recovered {_ct(now)}.",
                }
                self._emit(item)
            self.failures, self.first_failure, self.incident = 0, None, None

    def unknown_caller(self, identity, reason):
        """identity: the caller's own claimed id (credential id, OAuth subject or client label); only its fingerprint is
        kept or sent."""
        now, fp = self.clock(), fingerprint(identity)
        with self.lock:
            seen = [t for t in self.unknown.get(fp, []) if now - t < WINDOW] + [now]
            self.unknown[fp] = seen
            hour = int(now // WINDOW)
            if len(seen) >= UNKNOWN_AFTER and (fp, hour) not in self.alerted:
                self.alerted = {(f, h) for f, h in self.alerted if h >= hour - 1} | {(fp, hour)}
                self._emit(
                    {
                        "key": f"memory-unknown-caller-{fp}-{hour}",
                        "subject": f"Unknown caller refused {len(seen)} times in an hour (fingerprint {fp})",
                        "details": f"Last refusal {_ct(now)}; code {reason}. The caller is not a registered client; it stays refused. Registered clients and grant drills are never counted here.",
                    }
                )
