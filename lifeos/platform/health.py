"""The health model: what pages, what only shows, and when an incident closes (docs/platform/health-model.md).

Pure logic, no clock. Severities: PAGE (phone push), WARN (Daily Report card only), INFO. A push goes out when an
incident's state changes; a run-based incident closes after 3 finished runs passed. The same item failing with the
same fixed code on consecutive runs is a known state: WARN from the second run, ONE PAGE after 7 days."""
import datetime as dt
import re
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass

PAGE, WARN, INFO = "page", "warn", "info"
RECOVER_PASSES = 3
KNOWN_AFTER_RUNS = 2
ESCALATE_DAYS = 7
ESCALATION_FAMILY = "known_state_expired"
STATE_KEY = re.compile(r"[a-z][a-z0-9_]{1,63}")


def recovered(conclusions: Sequence[str], passes: int = RECOVER_PASSES) -> bool:
    """True when the newest `passes` finished runs (newest first; cancelled and manual runs already left out) all
    succeeded."""
    return len(conclusions) >= passes and all(c == "success" for c in conclusions[:passes])


def state_key(family: str, *parts: str) -> str:
    """The key an alert is raised and tracked under: a registry family plus the item, lowercase words joined by
    `_`."""
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
    new: list
    known: list  # WARN, no push, never red
    escalate: list
    cleared: list


class KnownStates:

    def __init__(self, store: MutableMapping[str, dict]):
        self.store = store

    def observe(self, failures: Mapping[str, str], now: dt.datetime) -> Observation:
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
    return [f"WARN {k.item}: {k.code}, failing on {k.runs} runs over {k.days(now)} days" for k in known]


def escalate(observation: Observation, family: str, push: Callable[..., bool]) -> int:
    """Send the one PAGE for each known state that crossed ESCALATE_DAYS; -> pushes accepted."""
    sent = 0
    for k in observation.escalate:
        if push(state_key(ESCALATION_FAMILY, family), f"A known failure is {ESCALATE_DAYS} days old", f"{k.item} still fails with {k.code} after {ESCALATE_DAYS} days. It never paged before this."):
            sent += 1
    return sent
