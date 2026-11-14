"""LIFE-690: the one health model: what pages, what only shows, and when an incident closes (docs/platform/health-model.md).

Pure logic; no OS knowledge, no clock of its own. Three severities: PAGE (a phone push), WARN (shown on the Daily Report card, never a push) and INFO (shown only). An incident has two states, open and recovered, and a push goes out when the state changes;
a PAGE incident that is still open and whose newest run is still failing may remind, never more often than every 3 hours (gate.REMIND_HOURS). A run-based incident closes once the last 3 finished runs passed (`recovered`).
A permanent known state is the same item failing with the same fixed code on consecutive runs: from the second run it is WARN, it never turns a job red and never pushes, and it becomes ONE PAGE when it is still failing after 7 days.
Items are opaque ids chosen by the caller (never a title, a URL or a person) and codes are fixed codes, so nothing here can carry content. A new source-health alert (Action Inbox) adopts this by calling `KnownStates.observe` once per run."""
import datetime as dt
import re
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass

PAGE, WARN, INFO = "page", "warn", "info"
RECOVER_PASSES = 3                        # a run-based incident closes after this many finished runs in a row passed (LIFE-681, applied everywhere)
KNOWN_AFTER_RUNS = 2                      # the same item with the same code on this many consecutive runs is a permanent known state
ESCALATE_DAYS = 7                         # a known state still failing after this long becomes one PAGE, so it cannot be forgotten
ESCALATION_FAMILY = "known_state_expired"  # the registry family of that one PAGE (alert_registry: known_state_expired_*)
STATE_KEY = re.compile(r"[a-z][a-z0-9_]{1,63}")


def recovered(conclusions: Sequence[str], passes: int = RECOVER_PASSES) -> bool:
    """True when the newest `passes` finished runs (conclusions, newest first; cancelled and manual runs already left out by the caller) all succeeded."""
    return len(conclusions) >= passes and all(c == "success" for c in conclusions[:passes])


def state_key(family: str, *parts: str) -> str:
    """The key an alert is raised and tracked under: a registry family plus the item, lowercase words joined by `_`. Raises ValueError for an empty or malformed key, so a push without a state key cannot be built."""
    key = "_".join(p for p in (family, *parts) if p)
    if not isinstance(family, str) or not STATE_KEY.fullmatch(key):
        raise ValueError("STATE_KEY_INVALID")
    return key


@dataclass(frozen=True)
class Known:
    item: str
    code: str
    first_seen: dt.datetime
    runs: int

    def days(self, now: dt.datetime) -> int:
        return int((now - self.first_seen).total_seconds() // 86400)


@dataclass
class Observation:
    new: list            # items on their first run with this code: the caller's normal failure path applies
    known: list          # Known: WARN, no push, never red
    escalate: list       # Known: still failing after ESCALATE_DAYS and not yet paged: exactly one PAGE each
    cleared: list        # items that no longer fail


class KnownStates:
    """Tracks permanent known states. `store` is any dict-like of JSON-safe records ({item: {"code", "first_seen", "runs", "escalated"}}) the caller persists wherever it already keeps state."""

    def __init__(self, store: MutableMapping[str, dict]):
        self.store = store

    def observe(self, failures: Mapping[str, str], now: dt.datetime) -> Observation:
        """One run: `failures` maps each item that failed this run to its fixed code. Items missing from it have recovered (cleared)."""
        seen = Observation([], [], [], [])
        for item, code in failures.items():
            record = self.store.get(item)
            if record and record.get("code") == code:
                record = {**record, "runs": int(record.get("runs", 1)) + 1}
            else:
                record = {"code": code, "first_seen": now.isoformat(), "runs": 1, "escalated": False}
            known = Known(item, code, dt.datetime.fromisoformat(record["first_seen"]), record["runs"])
            if record["runs"] < KNOWN_AFTER_RUNS:
                seen.new.append(item)
            else:
                seen.known.append(known)
                if (now - known.first_seen) >= dt.timedelta(days=ESCALATE_DAYS) and not record["escalated"]:
                    record["escalated"] = True
                    seen.escalate.append(known)
            self.store[item] = record
        for item in [i for i in self.store if i not in failures]:
            del self.store[item]
            seen.cleared.append(item)
        return seen


def card_lines(known: Sequence[Known], now: dt.datetime) -> list[str]:
    """The WARN lines for the Daily Report card: counts and fixed codes only."""
    return [f"WARN {k.item}: {k.code}, failing on {k.runs} runs over {k.days(now)} days" for k in known]


def escalate(observation: Observation, family: str, push: Callable[..., bool]) -> int:
    """Send the one PAGE for each known state that crossed ESCALATE_DAYS; -> pushes accepted. `push` is `alerts.push` (key, title, body); the key is known_state_expired_<family>, so it is registered and counted in the registry."""
    sent = 0
    for k in observation.escalate:
        if push(state_key(ESCALATION_FAMILY, family), f"A known failure is {ESCALATE_DAYS} days old", f"{k.item} still fails with {k.code} after {ESCALATE_DAYS} days. It never paged before this."):
            sent += 1
    return sent
