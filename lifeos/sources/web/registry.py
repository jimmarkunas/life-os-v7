"""The US Remote web source universe (employers, staffing agencies, discovery helpers). Public names and ATS identities only.

Config, not code: a source is one row. `status` says how V7 can read it today (see us_remote.json): ready | port | bespoke | discovery.
"""
import json
from pathlib import Path

PATH = Path(__file__).with_name("us_remote.json")
TIERS = ("employer", "staffing", "helper")
STATUSES = ("ready", "port", "bespoke", "discovery", "fallback")
PATHS = {"US Remote": PATH, "Scale-Up": Path(__file__).with_name("scale_up.json")}


class RegistryError(ValueError):
    pass


def load(path=PATH):
    data = json.loads(Path(path).read_text())
    seen = set()
    for row in data["sources"]:
        if row["tier"] not in TIERS or row["status"] not in STATUSES:
            raise RegistryError(f"{row.get('id')}: bad tier or status")
        if row["id"] in seen:
            raise RegistryError(f"duplicate source id {row['id']}")
        seen.add(row["id"])
        if row["status"] == "ready" and not (row.get("slug") or row.get("url")):
            raise RegistryError(f"{row['id']}: a ready board needs its slug or url")
        if row["status"] != "ready" and not (row.get("url") or row.get("slug")):
            raise RegistryError(f"{row['id']}: needs a url or slug")
    return data["sources"]


def enabled(status=None, tier=None, path=PATH):
    return [r for r in load(path) if r["enabled"] and (status is None or r["status"] == status) and (tier is None or r["tier"] == tier)]


def coverage(path=PATH):
    """{(tier, status): count} over enabled sources."""
    out = {}
    for row in enabled(path=path):
        out[(row["tier"], row["status"])] = out.get((row["tier"], row["status"]), 0) + 1
    return out


def for_lane(lane, status="ready"):
    """Enabled sources of one lane's universe (default: the ones V7 can list today)."""
    return enabled(status=status, path=PATHS[lane])
