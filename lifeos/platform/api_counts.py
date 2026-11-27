"""How many calls each outside service got, per stage: service names and numbers only, never a URL, id or payload.

`count(service)` is called once per HTTP attempt by the shared REST layer and by the Notion and Gmail clients. A
stage's calls are those made by its own thread (`begin` clears them, `take` returns and clears them); calls made by
a helper thread of a stage, or outside a stage, are only in `totals()`."""

import threading

_local = threading.local()
_lock = threading.Lock()
_totals = {}
NAMES = {"JIRA": "jira", "OUTLOOK": "graph", "TODOIST": "todoist", "GCAL": "gcal"}  # rest.call's prefix -> service


def count(service):
    service = NAMES.get(service, str(service).lower())
    mine = getattr(_local, "calls", None)
    if mine is None:
        mine = _local.calls = {}
    mine[service] = mine.get(service, 0) + 1
    with _lock:
        _totals[service] = _totals.get(service, 0) + 1


def begin():
    _local.calls = {}


def take():
    """-> {service: calls} made by this thread since `begin` (or the last `take`); clears them."""
    calls, _local.calls = getattr(_local, "calls", None) or {}, {}
    return dict(sorted(calls.items()))


def totals():
    with _lock:
        return dict(sorted(_totals.items()))
