"""Notion page and data-source ids come from the environment, never from source.

A run that needs a real page gets one JSON variable, LIFEOS_NOTION_IDS ({"NAME": "id"}); a single LIFEOS_NOTION_<NAME>
variable also works and wins. Without either the id is a synthetic hash of the name that matches nothing."""

from collections.abc import Mapping
import hashlib
import json
import os


def configured_id(name: str, environ: Mapping[str, str] = os.environ) -> str:
    """The real id for a name, or "" when neither variable holds it."""
    one = (environ.get("LIFEOS_NOTION_" + name) or "").strip()
    if one:
        return one
    try:
        mapped = json.loads(environ.get("LIFEOS_NOTION_IDS") or "{}").get(name)
    except (ValueError, AttributeError):
        mapped = None
    return str(mapped).strip() if mapped else ""


def notion_id(name: str) -> str:
    return configured_id(name) or hashlib.sha256(("synthetic:" + name).encode()).hexdigest()[:32]
