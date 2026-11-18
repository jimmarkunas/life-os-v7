"""Stage `accountability-artifacts` (LIFE-673, LIFE-840): the Knowledge center callout of Jim and of Matt is a directory of real deliverables, the verified Google Docs and Sheets of the artifact index (child 05),
not a task list. Jim's candidates are discovered from his JFM issues (descriptions and comments) and counted against the index; only an indexed, verified artifact is shown."""
from lifeos.accountability import common, index, read, region, render


def _build(client, block_id, person, project, jira, now, live, site, notion):
    listed = index.artifacts(notion, person)
    shown, unresolved = listed if listed is not None else (None, 0)
    parts, lines = render.artifact_directory(shown)
    stamp = render.stamp(now)
    out = region.write_parts(client, block_id, stamp, render.fit(parts), live)
    page = out.pop("summary_page")
    out["summary_written"] = region.write_summary(client, page, render.summary(f"Knowledge center ({person})", stamp, lines), live)
    out["artifacts"] = len(shown or [])
    out["unresolved"] = unresolved
    if person == "Jim":
        known = {a["file_id"] for a in shown or []}
        out["unindexed_candidates"] = len([f for f in read.google_files(jira, project) if f not in known])
    return out


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    return common.run_region("artifacts", _build, environ, jira, notion, now, live)
