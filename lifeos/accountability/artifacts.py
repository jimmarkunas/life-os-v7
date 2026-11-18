"""Stage `accountability-artifacts`: the Knowledge center callout of Jim and of Matt is a directory of verified Google Docs and Sheets (LIFE-673, LIFE-840).

The links come from the artifact index (child 05 of the V3 master), one native bullet each, not from a task list; the callout carries no Summary for Claude line. Jim's candidates are discovered from his JFM issues and counted against the index; only an indexed, verified artifact is shown.
"""
from lifeos.accountability import common, index, read, region, render


def _build(client, block_id, person, project, jira, now, live, site, notion):
    listed = index.artifacts(notion, person)
    shown, unresolved, excluded = listed if listed is not None else (None, 0, set())
    lines, _, bulleted = render.artifact_lines(shown)
    out = region.write_directory(client, block_id, render.stamp(now), lines, bulleted, live)
    out["artifacts"] = len(shown or [])
    out["unresolved"] = unresolved
    if person == "Jim":
        known = {a["file_id"] for a in shown or []} | excluded
        out["unindexed_candidates"] = len([f for f in read.google_files(jira, project) if f not in known])
    return out


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    return common.run_region("artifacts", _build, environ, jira, notion, now, live)
