"""Stage `accountability-week` (LIFE-671): "This week (Jim)" and "This week (Matt)" on Jim & Matt V3, from the person's Jira project."""
from lifeos.accountability import common, read, region, render


def _build(client, block_id, person, project, jira, now, live):
    today = now.date()
    lines = render.week_lines(read.week(jira, project, today), today)
    stamp = render.stamp(now)
    out = region.write_text(client, block_id, stamp, render.body(lines), live)
    page = out.pop("summary_page")
    out["summary_written"] = region.write_summary(client, page, render.summary(f"This week ({person})", stamp, lines), live)
    return out


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    return common.run_region("week", _build, environ, jira, notion, now, live)
