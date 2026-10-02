"""Which Jira boards V7 manages: JIRA_BOARDS="KEY:board_id[:all],..." (a secret; keys and ids stay out of the public repo)."""
import os

from lifeos.platform.jira import JiraError


def boards(environ=os.environ):
    """[(project_key, board_id, triage_all)]. 'all' widens triage from Task-level to every non-Epic work item."""
    raw = (environ.get("JIRA_BOARDS") or "").strip()
    if not raw:
        raise JiraError("JIRA_CONFIG_MISSING:JIRA_BOARDS")
    out = []
    for entry in raw.split(","):
        parts = [p.strip() for p in entry.split(":")]
        if len(parts) not in (2, 3) or not parts[0].isalnum() or not parts[1].isdigit() or (len(parts) == 3 and parts[2] != "all"):
            raise JiraError("JIRA_CONFIG_INVALID")
        out.append((parts[0], int(parts[1]), len(parts) == 3))
    if len({key for key, _, _ in out}) != len(out):
        raise JiraError("JIRA_CONFIG_INVALID")
    return tuple(out)
