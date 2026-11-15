"""Stage `accountability-artifacts` (LIFE-673): Knowledge center for Jim and Matt, linked artifacts and the current sprint's to-do lists (open and done apart)."""
from lifeos.accountability import common, read, region, render


def _build(client, block_id, person, project, jira, now, live, site):
    shown = read.artifacts(jira, project)
    art_parts, art_lines = render.artifact_parts(shown, site)
    todo_parts, todo_lines = render.todo_view(read.todos(jira, project), site, project)
    stamp = render.stamp(now)
    out = region.write_parts(client, block_id, stamp, render.fit(art_parts + [render.NL] + todo_parts), live)
    page = out.pop("summary_page")
    out["summary_written"] = region.write_summary(client, page, render.summary(f"Knowledge center ({person})", stamp, art_lines + todo_lines), live)
    out["artifacts"] = len(shown)
    return out


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    return common.run_region("artifacts", _build, environ, jira, notion, now, live)
