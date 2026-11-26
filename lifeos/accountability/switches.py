"""The five cutover rows of the Runtime Configuration source (MEJ_SPRINT_AUTOMATION, V3_WEEK_VIEW, V2_QUARTER_SHADOW,
V2_PROMOTION, V2_OFFICIAL), read so they fail CLOSED: on only when the value is exactly "on" (case and spaces
ignored).
"""
import os

SWITCHES = ("MEJ_SPRINT_AUTOMATION", "V3_WEEK_VIEW", "V2_QUARTER_SHADOW", "V2_PROMOTION", "V2_OFFICIAL")


def _read(environ):
    environ = os.environ if environ is None else environ
    try:
        return {"MEJ_SPRINT_AUTOMATION": environ.get("MEJ_SPRINT_AUTOMATION"), "V3_WEEK_VIEW": environ.get("V3_WEEK_VIEW"), "V2_QUARTER_SHADOW": environ.get("V2_QUARTER_SHADOW"),
                "V2_PROMOTION": environ.get("V2_PROMOTION"), "V2_OFFICIAL": environ.get("V2_OFFICIAL")}
    except (AttributeError, TypeError):
        return {}


def on(name, environ=None):
    return name in SWITCHES and str(_read(environ).get(name) or "").strip().lower() == "on"


def snapshot(environ=None):
    return {name: on(name, environ) for name in SWITCHES}
