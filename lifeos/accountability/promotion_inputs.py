"""The real inputs of the quarterly promotion check, read from the approved membership file and Jira.

Per board: the frozen list, the keys Jira returns for it, each completion date from the first Done transition in the
changelog, the previous quarter's published figures beside a fresh count of them, manual and automatic closures kept
apart, and every key the legacy goal list and the frozen list disagree on. Anything unreadable, missing or
contradictory leaves that board empty, which the check reports as a hard failure for that board; nothing is guessed."""

from datetime import date

from lifeos.accountability import closure, promotion, read, week
from lifeos.platform.jira import JiraError

ONE_PAGE = 50


def _members(jira, keys):
    """-> {key: issue fields} for the listed keys that Jira returns as a Task or Story."""
    found = {}
    for at in range(0, len(keys), ONE_PAGE):
        for issue in read.search(jira, f"key in ({','.join(keys[at : at + ONE_PAGE])})", read.CHILD_FIELDS):
            fields = issue.get("fields") or {}
            kind = fields.get("issuetype") or {}
            if not kind.get("subtask") and kind.get("name") in ("Task", "Story"):
                found[issue["key"]] = fields
    return found


def _done(fields):
    return ((fields.get("status") or {}).get("statusCategory") or {}).get("key") == "done"


def _completion(jira, key, fields, first, last):
    try:
        day = read._first_verified_done(jira, key, (fields.get("status") or {}).get("id"))
    except JiraError:
        return {"date": None, "source": "unverified"}
    return {"date": day.isoformat(), "source": "changelog", "ambiguous": not first <= day <= last}


def _legacy_keys(jira, goal_keys):
    """Direct children of the legacy goal Epics, as the v1 figures count them."""
    if not goal_keys:
        return set()
    keys = set()
    for issue in read.search(jira, f"parent in ({','.join(sorted(goal_keys))})", read.CHILD_FIELDS):
        kind = (issue.get("fields") or {}).get("issuetype") or {}
        if not kind.get("subtask") and kind.get("name") != "Epic":
            keys.add(issue["key"])
    return keys


def _history(jira, board, year, n, today, manifest):
    """-> {"before": published figures, "after": a fresh count of them} for the previous quarter, or None."""
    first, last = read.quarter_bounds(*read.previous(year, n))
    row = read._frozen_row(first, manifest, board)
    snapshot = row.get("snapshot")
    gaps = row.get("gaps") if isinstance(row.get("gaps"), dict) else {}
    goals = {a["goal"] for a in row["tasks"].values()} - set(gaps)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("figures"), dict) or not goals:
        return None
    after = read._mej_v2_tally(jira, goals, first, last, manifest, board, today, recount=True)
    before = {goal: snapshot["figures"].get(goal) for goal in sorted(goals)}
    return {"before": before, "after": {goal: after[goal] for goal in sorted(goals)}}


def board(jira, name, year, n, environ=None, today=None, manifest=None):
    """-> one board's inputs, or {} when any of it cannot be read."""
    first, last = read.quarter_bounds(year, n)
    since = week._since(environ)
    try:
        row = read._frozen_row(first, manifest, name)
        frozen = sorted(row["tasks"])
        members = _members(jira, frozen)
        done = {k: f for k, f in members.items() if _done(f) and closure.classify(f) != "cancelled"}
        classes = {k: closure.classify(f, read._closed_at(f), since) for k, f in done.items()}
        manual = sorted(k for k, c in classes.items() if c == "delivered_manual")
        auto = sorted(k for k, c in classes.items() if c == "delivered_auto")
        person = dict((project, who) for who, project in read.PEOPLE)[name]
        goal_epics = {e["key"] for e in read.epics(jira, year, n) if e["person"] == person}
        explanations = row.get("explanations") if isinstance(row.get("explanations"), dict) else {}
        differing = sorted(_legacy_keys(jira, goal_epics) ^ set(frozen))
        return {
            "frozen": frozen,
            "counted": sorted(members),
            "goals": {key: [row["tasks"][key]["goal"]] for key in frozen},
            "completions": {k: _completion(jira, k, f, first, last) for k, f in done.items()},
            "history": _history(jira, name, year, n, today or date.today(), manifest),
            "recurring": {
                "manual": len(manual),
                "auto": len(auto),
                "total": len(done),
                "manual_keys": manual,
                "auto_keys": auto,
            },
            "discrepancies": [{"key": key, "explanation": explanations.get(key)} for key in differing],
        }
    except JiraError:
        return {}


def readers(jira, environ=None, today=None, year=promotion.TARGET[0], n=promotion.TARGET[1], manifest=None):
    """The `readers` callable of promotion.run_if_due."""

    def read_all():
        return {
            "quarter": promotion.quarter_key(year, n),
            "boards": {name: board(jira, name, year, n, environ, today, manifest) for name in promotion.BOARDS},
        }

    return read_all
