"""Which Jira boards V7 manages: JIRA_BOARDS="KEY:board_id[:flag...],..." (a secret; keys and ids stay out of the public repo).
Flags: all = triage every non-Epic item; readonly = snapshot only, never sprint rollover (e.g. a team board until opted in)."""
import os

from lifeos.platform.jira import JiraError


def boards(environ=os.environ):
    """[(project_key, board_id, triage_all, readonly)]."""
    raw = (environ.get("JIRA_BOARDS") or "").strip()
    if not raw:
        raise JiraError("JIRA_CONFIG_MISSING:JIRA_BOARDS")
    out = []
    for entry in raw.split(","):
        parts = [p.strip() for p in entry.split(":")]
        flags = parts[2:]
        if len(parts) < 2 or not parts[0].isalnum() or not parts[1].isdigit() or any(f not in ("all", "readonly") for f in flags) \
                or len(set(flags)) != len(flags):
            raise JiraError("JIRA_CONFIG_INVALID")
        out.append((parts[0], int(parts[1]), "all" in flags, "readonly" in flags))
    if len({entry[0] for entry in out}) != len(out):
        raise JiraError("JIRA_CONFIG_INVALID")
    return tuple(out)


def owned(sprints, project, keys):
    """Sprints on a (possibly shared) board that belong to this project: named with its key (V7 names its sprints that way);
    otherwise those not named for another configured project. Boards whose filter spans projects list each other's sprints."""
    named = [s for s in sprints if str(s.get("name") or "").startswith(project)]
    if named:
        return named
    others = [k for k in keys if k != project]
    return [s for s in sprints if not any(str(s.get("name") or "").startswith(k) for k in others)]
