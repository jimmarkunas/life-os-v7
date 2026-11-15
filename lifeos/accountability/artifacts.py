"""Stage `accountability-artifacts` (LIFE-673): Knowledge center for Jim and Matt, artifact links and the current sprint's to-do lists."""
from lifeos.accountability import common, read, region, render


def _build(client, block_id, person, project, jira, now, live):
    shown = read.artifacts(jira, project)
    lines = render.artifact_lines(shown) + [""] + render.todo_lines(read.todos(jira, project))
    stamp = render.stamp(now)
    out = region.write_text(client, block_id, stamp, render.body(lines), live)
    page = out.pop("summary_page")
    out["summary_written"] = region.write_summary(client, page, render.summary(f"Knowledge center ({person})", stamp, lines), live)
    out["artifacts"] = len(shown)
    return out


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    return common.run_region("artifacts", _build, environ, jira, notion, now, live)
