"""LIFE-870 (Jim, comment 13867): the private Buzz relay pilot as one more community ("pilot") of the governed MCP service, behind BUZZ_RELAY=pilot|hosted.

hosted (default): nothing is added, so a call naming the pilot community is `community_not_authorized`. pilot: each connector identity that has its own pilot key (BUZZ_KEY_<IDENTITY>_PILOT, a systemd credential)
reaches the fixed loopback pilot address with that key, and may post to the channel or channels (comma list) in BUZZ_PILOT_CHANNEL. The hosted communities are not touched. Inputs: the service's environment, its relays by identity, and the
key decoder (lifeos.buzz.nip98.secret_from, passed in because platform imports no other layer). Output: the identities that now reach the pilot. A bad setting raises ValueError(<setting name>); no value is ever put in a message."""
import re

from lifeos.platform import relay_choice

COMMUNITY = "pilot"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def wire(env, relays, decode):
    if relay_choice.mode(env) != "pilot":
        return ()
    channels = [c.strip().lower() for c in (env.get("BUZZ_PILOT_CHANNEL") or "").split(",") if c.strip()]            # LIFE-927: one channel or a comma list (the four that match the hosted names)
    if not channels or not all(UUID.match(c) for c in channels):
        raise ValueError("BUZZ_PILOT_CHANNEL")
    keys, wired = {"claude": env.get("BUZZ_KEY_CLAUDE_PILOT"), "chatgpt": env.get("BUZZ_KEY_CHATGPT_PILOT"), "duet": env.get("BUZZ_KEY_DUET_PILOT")}, []
    for identity, relay in relays.items():
        value = keys.get(identity)
        if value:
            relay.urls[COMMUNITY], relay.keys[COMMUNITY] = relay_choice.PILOT_URL, decode(value)
            relay.writes.update((COMMUNITY, c) for c in channels)
            wired.append(identity)
    return tuple(wired)
