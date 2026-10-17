"""D202: rebuild boards.json from an Open Jobs board list (slugs.json, CC0). Run by hand when a newer list is wanted; V7 never downloads it.

    python -m lifeos.sources.web.boards_seed path/to/slugs.json

The extras and skip lists already in boards.json are kept; only the seed is replaced.
"""
import json
import re
import sys

from lifeos.sources.web import registry

SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")          # goes into an API path: nothing that could change the URL


def build(slugs, current):
    """slugs: the parsed Open Jobs list ({"generated_at", "ats": {kind: [slug, ...]}}). current: the boards.json now in the repo."""
    room = registry.MAX_ID - 3                                 # "gh-" etc.
    seed = {k: sorted({s for s in slugs["ats"].get(k, []) if isinstance(s, str) and SLUG.match(s) and len(s) <= room}, key=str.lower)
            for k in registry.BOARD_KINDS}
    return {"about": current.get("about"), "seed_generated_at": slugs.get("generated_at"), "seed": seed,
            "extras": current.get("extras", []), "skip": current.get("skip", [])}


def main(argv):
    slugs = json.loads(open(argv[1], encoding="utf-8").read())
    current = json.loads(registry.BOARDS_PATH.read_text())
    out = build(slugs, current)
    registry.BOARDS_PATH.write_text(json.dumps(out, indent=0, ensure_ascii=True) + "\n")
    print({k: len(v) for k, v in out["seed"].items()}, "extras", len(out["extras"]))


if __name__ == "__main__":
    main(sys.argv)
