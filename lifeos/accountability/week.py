"""Stage `accountability-week` (LIFE-671): This week for Jim and Matt on V3; tickets link to Jira, open and done apart."""
from lifeos.accountability import common, read, region, render


def _build(client, block_id, person, project, jira, now, live, site):
    today = now.date()
    parts, lines = render.week_view(read.week(jira, project, today), today, site)
    stamp = render.stamp(now)
    out = region.write_parts(client, block_id, stamp, render.fit(parts), live)
    page = out.pop("summary_page")
    out["summary_written"] = region.write_summary(client, page, render.summary(f"This week ({person})", stamp, lines), live)
    return out


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    return common.run_region("week", _build, environ, jira, notion, now, live)
