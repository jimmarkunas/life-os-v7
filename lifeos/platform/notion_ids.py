"""Notion page and data-source ids come from the environment, never from source (LIFE-338).

The public repo holds names only. A run that needs a real page sets LIFEOS_NOTION_<NAME> (GitHub Actions variable);
without it the id is a synthetic hash of the name that matches nothing, so tests and forks stay safe.
"""
import hashlib
import os


def notion_id(name):
    """The real id from LIFEOS_NOTION_<name>, else a stable synthetic one (distinct per name) that matches nothing."""
    return (os.environ.get("LIFEOS_NOTION_" + name) or "").strip() or hashlib.sha256(("synthetic:" + name).encode()).hexdigest()[:32]
