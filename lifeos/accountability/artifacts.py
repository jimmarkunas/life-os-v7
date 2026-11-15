"""Stage `accountability-artifacts` (LIFE-673): the Knowledge center for Jim and Matt, from Jira issues labelled artifact, as "title — link" lines."""
from lifeos.accountability import common, read, region, render


def _build(client, block_id, person, project, jira, now, live):
    lines = render.artifact_lines(read.artifacts(jira, project))
    stamp = render.stamp(now)
    out = region.write_text(client, block_id, stamp, render.body(lines), live)
    page = out.pop("summary_page")
    out["summary_written"] = region.write_summary(client, page, render.summary(f"Knowledge center ({person})", stamp, lines), live)
    out["artifacts"] = 0 if lines == [render.NO_ARTIFACTS] else len(lines)
    return out


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    return common.run_region("artifacts", _build, environ, jira, notion, now, live)
