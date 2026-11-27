"""The private Buzz relay pilot as one more community of the governed MCP service (BUZZ_RELAY=pilot|hosted).

hosted adds nothing; pilot gives each identity with its own pilot key (BUZZ_KEY_<IDENTITY>_PILOT) access to the pilot
address and the channels in BUZZ_PILOT_CHANNEL. A bad setting raises
ValueError(<setting name>), never a value.
"""

import re

from lifeos.platform import relay_choice

COMMUNITY = "pilot"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def wire(env, relays, decode):
    if relay_choice.mode(env) != "pilot":
        return ()
    channels = [c.strip().lower() for c in (env.get("BUZZ_PILOT_CHANNEL") or "").split(",") if c.strip()]
    if not channels or not all(UUID.match(c) for c in channels):
        raise ValueError("BUZZ_PILOT_CHANNEL")
    keys, wired = (
        {
            "claude": env.get("BUZZ_KEY_CLAUDE_PILOT"),
            "chatgpt": env.get("BUZZ_KEY_CHATGPT_PILOT"),
            "duet": env.get("BUZZ_KEY_DUET_PILOT"),
            "openclaw": env.get("BUZZ_KEY_OPENCLAW_PILOT"),
        },
        [],
    )
    for identity, relay in relays.items():
        value = keys.get(identity)
        if value:
            relay.urls[COMMUNITY], relay.keys[COMMUNITY] = relay_choice.PILOT_URL, decode(value)
            relay.writes.update((COMMUNITY, c) for c in channels)
            wired.append(identity)
    return tuple(wired)
