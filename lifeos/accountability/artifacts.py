"""Stage `accountability-artifacts`: verified Google Docs and Sheets from the artifact index, as a directory in each person's Knowledge center callout.
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
