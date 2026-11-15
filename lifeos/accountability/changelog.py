"""Stage `accountability-changelog` (LIFE-673): Canon / Change Log on Jim & Matt V3. Appends one dated line when a quarter begins; earlier lines are never edited."""
import os
from datetime import datetime

from lifeos.accountability import common, read, region, render


def run(limit, live, environ=None, notion=None, now=None):
    environ = os.environ if environ is None else environ
    now = now or datetime.now(common.CHI)
    notion = notion or common.notion_writer(environ)
    block_id = region.address(notion, "shared.changelog")
    client = region.scoped(notion, block_id)
    year, n, _, _ = read.quarter(now.date())
    found = region.locate(client, block_id)
    marker = f"Q{n} {year} began"
    have = [region.text(b) for b in found["middle"]]
    out = {"appended": 0, "lines": len(have)}
    if not any(marker in line for line in have):
        out.update(region.append_bullet(client, block_id, render.changelog_line(now.date(), year, n), live))
    stamp = render.stamp(now)
    out["summary_page"] = out.pop("summary_page", found["summary"])
    last = out.get("last") or (have[-1] if have else "")
    out["summary_written"] = region.write_summary(client, out.pop("summary_page"), render.summary("Canon / Change Log", stamp, [f"{out['lines']} lines, oldest first. Latest: {last}"]), live)
    return {"shared": out}
