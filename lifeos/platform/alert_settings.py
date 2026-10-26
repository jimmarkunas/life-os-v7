"""Alert settings (LIFE-75): what Jim set on the Alert settings page, read from the repository variables every run already receives as environment.
ALERTS_ENABLED is the master switch (LIFE-71): alerts send only when it is exactly "on"; unset, empty or anything else means OFF. ALERT_SETTINGS is one small JSON document,
{"v": 1, "channels": {"<registered alert key>": ["ntfy", "telegram", "whatsapp"]}}, that replaces an alert's default channels. An unreadable or invalid document, an unknown key and an
unknown channel are ignored (the registry defaults apply), and a PAGE alert always keeps the phone: the page cannot untick it. The page that edits the document is served by the
Close service on Oracle (lifeos/sources/alerts_app.py); this module is the one reader of what it saves."""
import json
import os

from lifeos.platform import alert_registry as registry

ENABLED_VARIABLE, SETTINGS_VARIABLE = "ALERTS_ENABLED", "ALERT_SETTINGS"
MAX_BYTES = 8000                    # a repository variable holds 48 KB; the document stays far under it


def enabled(environ=None):
    """True only when ALERTS_ENABLED is exactly "on" (any case, spaces ignored). The safe default is OFF."""
    return ((environ if environ is not None else os.environ).get(ENABLED_VARIABLE) or "").strip().lower() == "on"


def parse(raw):
    """-> {registry key: tuple of channels} for the valid entries of one document; {} for anything unreadable. Channels come back in registry order."""
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_BYTES:
        return {}
    try:
        doc = json.loads(raw)
    except ValueError:
        return {}
    channels = doc.get("channels") if isinstance(doc, dict) and doc.get("v") == 1 else None
    if not isinstance(channels, dict):
        return {}
    out = {}
    for key, wanted in channels.items():
        if key in registry.REGISTRY and isinstance(wanted, list) and all(isinstance(c, str) for c in wanted):
            out[key] = tuple(c for c in registry.CHANNELS if c in wanted)
    return out


def load(environ=None):
    return parse((environ if environ is not None else os.environ).get(SETTINGS_VARIABLE) or "")


def channels(key, environ=None):
    """-> the channels this alert reaches now: Jim's choice if he made one, else the registry default; always the phone for a PAGE alert; () for a key that is not registered."""
    spec = registry.lookup(key)
    if spec is None:
        return ()
    chosen = load(environ).get(registry.pattern_of(key))
    result = tuple(chosen) if chosen is not None else tuple(spec.channels)
    if spec.severity == registry.PAGE and "ntfy" not in result:
        result = ("ntfy",) + result
    return result


def document(chosen):
    """The JSON text to save for {registry key: channels}: only valid keys and channels, only entries that differ from the registry default, sorted; "" when nothing differs."""
    keep = {}
    for key, wanted in sorted(chosen.items()):
        spec = registry.REGISTRY.get(key)
        if spec is None:
            continue
        ordered = tuple(c for c in registry.CHANNELS if c in wanted)
        if spec.severity == registry.PAGE and "ntfy" not in ordered:
            ordered = ("ntfy",) + ordered
        if ordered != tuple(spec.channels):
            keep[key] = list(ordered)
    return json.dumps({"v": 1, "channels": keep}, sort_keys=True, separators=(",", ":")) if keep else ""
