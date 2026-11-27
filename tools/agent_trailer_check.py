"""Fail a pull request whose commits or description lack an `Agent:` trailer (policy section 3, docs/policy/agent-coordination.md).

    python tools/agent_trailer_check.py        reads the pull request event from GITHUB_EVENT_PATH; GITHUB_TOKEN is optional

Every commit message and the pull request description must carry one line `Agent: MC01`, `Agent: OC`, `Agent: Codex` or
`Agent: Jim`. Only counts and short commit ids are printed. A pull request event is the only one checked; the merge queue
and pushes to main pass. If GitHub's commit list cannot be read the check warns and passes, so an outage cannot hold up
every merge."""

import json
import os
import re
import sys
import urllib.request

TRAILER = re.compile(r"^Agent: (MC01|OC|Codex|Jim)[ \t]*$", re.MULTILINE)
NAMES = "MC01, OC, Codex or Jim"


def missing(body, commits):
    """-> (description_ok, [short ids of commits without the trailer]) for a description and [(sha, message)]."""
    return bool(TRAILER.search(body or "")), [sha[:7] for sha, message in commits if not TRAILER.search(message or "")]


def fetch_commits(repo, number, token, opener=urllib.request.urlopen):
    commits, page = [], 1
    while True:
        url = f"https://api.github.com/repos/{repo}/pulls/{number}/commits?per_page=100&page={page}"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "lifeos-agent-trailer-check"}
        if token:
            headers["Authorization"] = "Bearer " + token
        with opener(urllib.request.Request(url, headers=headers), timeout=30) as response:
            rows = json.loads(response.read())
        commits += [(row["sha"], row["commit"]["message"]) for row in rows]
        if len(rows) < 100:
            return commits
        page += 1


def run(event_name, event, repo, token, fetch=fetch_commits, out=sys.stdout):
    """-> exit code: 0 passes (or skips), 1 fails."""
    if event_name != "pull_request":
        out.write("agent trailer: skipped (not a pull request event)\n")
        return 0
    pull = event["pull_request"]
    try:
        commits = fetch(repo, pull["number"], token)
    except Exception:  # noqa: BLE001
        out.write("::warning::agent trailer: the commit list could not be read, so the commits were not checked\n")
        commits = None
    body_ok, bad = missing(pull.get("body"), commits or [])
    if body_ok and not bad:
        out.write("agent trailer: ok (%s commits and the description)\n" % ("?" if commits is None else len(commits)))
        return 0
    if not body_ok:
        out.write(
            f"::error::the pull request description has no `Agent:` trailer line ({NAMES}); add `Agent: MC01` (or yours)\n"
        )
    if bad:
        out.write(
            f"::error::{len(bad)} commit(s) have no `Agent:` trailer ({', '.join(bad[:10])}); add it as the last line of the message\n"
        )
    return 1


if __name__ == "__main__":
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        payload = json.load(handle)
    sys.exit(
        run(
            os.environ.get("GITHUB_EVENT_NAME", ""),
            payload,
            os.environ.get("GITHUB_REPOSITORY", ""),
            os.environ.get("GITHUB_TOKEN", ""),
        )
    )
