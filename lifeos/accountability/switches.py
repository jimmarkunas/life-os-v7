"""LIFE-918: the five cutover rows of the Runtime Configuration source, read as plain values that fail CLOSED.

MEJ_SPRINT_AUTOMATION, V3_WEEK_VIEW, V2_QUARTER_SHADOW, V2_PROMOTION and V2_OFFICIAL are on only when the value is exactly "on" (case and spaces ignored); unset, empty, "off" or anything else is off, so an
unreadable or unknown row can never turn anything on. There is no preview-block row any more (Jim, Nov 25 2026, comment 14216: the v2 preview lives inside the existing Quarterly Goals & Review blocks, found by the V3 block ID register). They come from the existing settings
path (lifeos.platform.settings fills the environment from the Runtime Configuration source); nothing here reads Notion, writes a setting or starts anything. Names and booleans only, never a value in a log."""
import os

SWITCHES = ("MEJ_SPRINT_AUTOMATION", "V3_WEEK_VIEW", "V2_QUARTER_SHADOW", "V2_PROMOTION", "V2_OFFICIAL")


def _read(environ):
    """The raw values, or all empty when the environment cannot be read."""
    environ = os.environ if environ is None else environ
    try:
        return {"MEJ_SPRINT_AUTOMATION": environ.get("MEJ_SPRINT_AUTOMATION"), "V3_WEEK_VIEW": environ.get("V3_WEEK_VIEW"), "V2_QUARTER_SHADOW": environ.get("V2_QUARTER_SHADOW"),
                "V2_PROMOTION": environ.get("V2_PROMOTION"), "V2_OFFICIAL": environ.get("V2_OFFICIAL")}
    except (AttributeError, TypeError):
        return {}


def on(name, environ=None):
    """True only when the named switch row is exactly "on"; every other value, and any name that is not one of the five switches, is off."""
    return name in SWITCHES and str(_read(environ).get(name) or "").strip().lower() == "on"


def snapshot(environ=None):
    """{switch name: on/off} for the five switches; for a receipt, never a value."""
    return {name: on(name, environ) for name in SWITCHES}
