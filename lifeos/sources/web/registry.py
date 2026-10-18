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


BOARDS_PATH = Path(__file__).with_name("boards.json")
BOARD_KINDS = {"greenhouse": "gh", "ashby": "ab", "lever": "lv"}    # D202: the seed ATS families and their id prefixes
MAX_ID = 36                                                          # "web:" + id fits the 40-character source column


def company_from_slug(slug):
    """A readable fallback name ("keeper-security" -> "Keeper Security"); a Greenhouse board's own name replaces it on each job."""
    return " ".join(w[:1].upper() + w[1:] for w in slug.replace("_", "-").replace(".", "-").split("-") if w) or slug


def boards(path=BOARDS_PATH):
    """D202: the employer boards seeded from the Open Jobs board list (CC0) plus the extras, minus `skip` and minus any board a curated
    universe (us_remote.json, scale_up.json) already reads, so no board is read twice. Rows are ready employer boards on a slow refresh."""
    from lifeos.platform import limits                                   # noqa: PLC0415
    data = json.loads(Path(path).read_text())
    known = {(r["kind"], str(r.get("slug") or "").lower()) for p in PATHS.values() for r in load(p) if r.get("slug")}
    skip = {(s.split(":", 1)[0], s.split(":", 1)[1].lower()) for s in data.get("skip", [])}
    rows, seen = [], set()
    pairs = [(e["kind"], e["slug"], e["company"]) for e in data.get("extras", [])] + [(k, slug, None) for k in BOARD_KINDS for slug in data["seed"].get(k, [])]   # an extra's name wins
    for kind, slug, company in pairs:
        key = (kind, slug.lower())
        if kind not in BOARD_KINDS or key in known or key in skip or key in seen:
            continue
        seen.add(key)
        ident = f"{BOARD_KINDS[kind]}-{slug.lower()}"
        if len(ident) > MAX_ID:
            raise RegistryError(f"{ident}: longer than {MAX_ID} characters")
        rows.append({"id": ident, "company": company or company_from_slug(slug), "tier": "employer", "kind": kind, "status": "ready",
                     "enabled": True, "slug": slug, "refresh_hours": limits.BOARDS_REFRESH_HOURS, "hot_hours": limits.BOARDS_HOT_HOURS, "quiet": True})
    return rows
