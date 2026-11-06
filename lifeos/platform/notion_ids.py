"""Notion page and data-source ids come from the environment, never from source (LIFE-338).

The public repo holds names only. A run that needs a real page gets one JSON variable, LIFEOS_NOTION_IDS (a GitHub Actions
variable shaped {"NAME": "id"}); a single LIFEOS_NOTION_<NAME> variable also works and wins. Without either the id is a
synthetic hash of the name that matches nothing, so tests and forks stay safe.
"""
import hashlib
import json
import os


def notion_id(name):
    one = (os.environ.get("LIFEOS_NOTION_" + name) or "").strip()
    if one:
        return one
    try:
        mapped = json.loads(os.environ.get("LIFEOS_NOTION_IDS") or "{}").get(name)
    except (ValueError, AttributeError):
        mapped = None
    return str(mapped).strip() if mapped else hashlib.sha256(("synthetic:" + name).encode()).hexdigest()[:32]
