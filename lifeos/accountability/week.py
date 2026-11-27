"""Stage `accountability-week`: This week for Jim and Matt on V3.

With V3_WEEK_VIEW exactly "on" (fail closed), BOTH boards get the sprint view as native blocks
(lifeos.accountability.weekview); otherwise the plain weekly view."""
import os

from lifeos.accountability import common, read, region, render, switches, weekview


def _build(client, block_id, person, project, jira, now, live, site, notion):
    today = now.date()
    parts, lines = render.week_view(read.week(jira, project, today), today, site)
    stamp = render.stamp(now)
    out = region.write_parts(client, block_id, stamp, render.fit(parts), live)
    page = out.pop("summary_page")
    out["summary_written"] = region.write_summary(client, page, render.summary(f"This week ({person})", stamp, lines), live)
    return out


def _build_v3(client, block_id, person, project, jira, now, live, site, notion):
    today = now.date()
    week = read.week(jira, project, today, v3_week_view=True)
    _, lines = render.week_view(week, today, site)
    stamp = render.stamp(now)
    out = region.write_tree(client, block_id, stamp, weekview.spec(week, today, site, project, now), live)
    page = out.pop("summary_page")
    out["summary_written"] = region.write_summary(client, page, render.summary(f"This week ({person})", stamp, lines), live)
    return out


def run(limit, live, environ=None, jira=None, notion=None, now=None):
    environ = os.environ if environ is None else environ
    return common.run_region("week", _build_v3 if switches.on("V3_WEEK_VIEW", environ) else _build, environ, jira, notion, now, live)
