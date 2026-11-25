"""LIFE-918: the six cutover rows of the Runtime Configuration source, read as plain values that fail CLOSED.

MEJ_SPRINT_AUTOMATION, V3_WEEK_VIEW, V2_QUARTER_SHADOW, V2_PROMOTION and V2_OFFICIAL are on only when the value is exactly "on" (case and spaces ignored); unset, empty, "off" or anything else is off, so an
unreadable or unknown row can never turn anything on. V3_PREVIEW_BLOCK_ID is the one address row: a Notion block id, or "" (no preview block) when unset or not a valid id. They come from the existing settings
path (lifeos.platform.settings fills the environment from the Runtime Configuration source); nothing here reads Notion, writes a setting or starts anything. Names and booleans only, never a value in a log."""
import os

from lifeos.platform import settings

SWITCHES = ("MEJ_SPRINT_AUTOMATION", "V3_WEEK_VIEW", "V2_QUARTER_SHADOW", "V2_PROMOTION", "V2_OFFICIAL")
PREVIEW_BLOCK = "V3_PREVIEW_BLOCK_ID"


def on(name, environ=None):
    """True only when the named switch row is exactly "on"; every other value, and any name that is not one of the five switches, is off."""
    if name not in SWITCHES:
        return False
    try:
        return str((environ if environ is not None else os.environ).get(name) or "").strip().lower() == "on"
    except (AttributeError, TypeError):
        return False


def preview_block(environ=None):
    """The V3 preview block id as 32 lowercase hex characters, or "" (no preview block) when unset or not a valid id."""
    try:
        value = str((environ if environ is not None else os.environ).get(PREVIEW_BLOCK) or "").strip()
    except (AttributeError, TypeError):
        return ""
    return value.replace("-", "").lower() if value and settings.ADDRESS.match(value) else ""


def snapshot(environ=None):
    """{switch name: on/off} for the five switches and whether a preview block is set; for a receipt, never a value."""
    return {**{name: on(name, environ) for name in SWITCHES}, "preview_block": bool(preview_block(environ))}
