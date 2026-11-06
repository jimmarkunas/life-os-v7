"""LIFE-320 connector allowlist: a pre_tool_call veto for Hermes connector tools.

The allowlist is the file `allowlist.txt` next to this module (one connector name per line, `#` starts a
comment). It is read on every check, so a change applies without a restart, and any problem with it means
"no connector is allowed". Stdlib only; the module imports nothing from Hermes.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

PREFIX = "connectors__"
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ALLOWLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "allowlist.txt")


def load_allowed(path: str = ALLOWLIST) -> frozenset:
    """Valid names from the file; anything unreadable or malformed gives an empty set (fail closed)."""
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return frozenset()
    names = set()
    for line in lines:
        item = line.split("#", 1)[0].strip().lower()
        if NAME.match(item):
            names.add(item)
    return frozenset(names)


def _block(why: str, allowed: frozenset) -> dict:
    listed = ", ".join(sorted(allowed)) or "none"
    return {"action": "block", "message": "connector-allowlist: %s. This profile may use only these connectors: %s." % (why, listed)}


def _targets(args: Any) -> Optional[list]:
    """Names asked for by a manage_connections call as (name, is_mcp) pairs; None when the shape is not understood."""
    if not isinstance(args, dict) or "connectors" not in args:
        return None
    raw = args["connectors"]
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return None
    found = []
    for item in raw:
        if isinstance(item, str):
            name, mcp = item, False
        elif isinstance(item, dict):
            name, mcp = item.get("name"), bool(item.get("mcp", False))
        else:
            return None
        if not isinstance(name, str) or not name.strip():
            return None
        found.append((name.strip().lower(), mcp))
    return found


def check(tool_name: Any, args: Any, allowed: frozenset) -> Optional[dict]:
    """None to let the call through, or a block directive."""
    if not isinstance(tool_name, str):
        return None
    name = tool_name.strip().lower()
    if name.startswith(PREFIX):
        slug = name[len(PREFIX):].split("__", 1)[0]
        if slug in allowed:
            return None
        return _block("the connector '%s' is not allowed" % (slug or "?"), allowed)
    if name == "manage_connections":
        targets = _targets(args)
        if targets is None:
            return _block("a connection request must name its connectors in a form this check understands", allowed)
        for target, mcp in targets:
            if mcp:
                return _block("MCP servers cannot be installed, enabled or authorized from this profile", allowed)
            if target not in allowed:
                return _block("the connector '%s' is not allowed" % target, allowed)
    return None


def _on_pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict]:
    try:
        return check(tool_name, args, load_allowed())
    except Exception:                                    # never let a bug in the check become an open door
        if isinstance(tool_name, str) and (tool_name.lower().startswith(PREFIX) or tool_name.lower() == "manage_connections"):
            return {"action": "block", "message": "connector-allowlist: the check failed, so the connector call was refused."}
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
