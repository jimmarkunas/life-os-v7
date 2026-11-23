"""LIFE-870: the private relay pilot is one more community of the governed MCP service, only behind BUZZ_RELAY=pilot, with the production Relay client and a recording transport (no network)."""
import json
import unittest

from lifeos.buzz import nip98
from lifeos.buzz.relay import BuzzError, Relay
from lifeos.platform import pilot_community, relay_choice

CHANNEL = "11111111-2222-4333-8444-555555555555"
CLAUDE_KEY, CHATGPT_KEY, HOSTED_KEY = "21" * 32, "22" * 32, "23" * 32


def relays(calls):
    def transport(method, url, headers, body, timeout=15):
        calls.append((url, headers["Authorization"]))
        return 200, json.dumps([]).encode()
    hosted = {"life-os": "https://x.communities.buzz.xyz"}
    return {i: Relay(dict(hosted), {"life-os": nip98.secret_from(HOSTED_KEY)}, transport=transport) for i in ("claude", "chatgpt")}


class PilotCommunity(unittest.TestCase):
    def test_hosted_is_the_default_and_the_pilot_community_is_refused(self):
        for env in ({}, {"BUZZ_RELAY": ""}, {"BUZZ_RELAY": "hosted", "BUZZ_PILOT_CHANNEL": CHANNEL, "BUZZ_KEY_CLAUDE_PILOT": CLAUDE_KEY}):
            calls, rs = [], None
            rs = relays(calls)
            self.assertEqual(pilot_community.wire(env, rs, nip98.secret_from), ())
            with self.assertRaises(BuzzError) as caught:
                rs["claude"].get_messages("pilot", CHANNEL)
            self.assertEqual((caught.exception.code, calls), ("community_not_authorized", []))

    def test_pilot_mode_wires_only_the_identity_that_has_a_pilot_key_to_the_loopback_address_and_one_channel(self):
        calls = []
        rs = relays(calls)
        env = {"BUZZ_RELAY": "pilot", "BUZZ_PILOT_CHANNEL": CHANNEL.upper(), "BUZZ_KEY_CLAUDE_PILOT": CLAUDE_KEY}
        self.assertEqual(pilot_community.wire(env, rs, nip98.secret_from), ("claude",))
        rs["claude"].get_messages("pilot", CHANNEL)
        self.assertEqual(calls[0][0], relay_choice.PILOT_URL + "/query")
        self.assertTrue(calls[0][1].startswith("Nostr "))
        self.assertEqual(rs["claude"].writes, {("pilot", CHANNEL)})
        with self.assertRaises(BuzzError) as caught:                                     # ChatGPT has no pilot key yet: the community is there but it has no identity on it
            rs["chatgpt"].get_messages("pilot", CHANNEL)
        self.assertEqual(caught.exception.code, "community_not_authorized")
        calls.clear()
        rs["claude"].get_messages("life-os", CHANNEL)                                    # a hosted community is untouched
        self.assertEqual(calls[0][0], "https://x.communities.buzz.xyz/query")

    def test_a_bad_mode_or_channel_stops_the_service_naming_only_the_setting(self):
        for env, name in (({"BUZZ_RELAY": "Pilot2"}, "BUZZ_RELAY"), ({"BUZZ_RELAY": "pilot"}, "BUZZ_PILOT_CHANNEL"), ({"BUZZ_RELAY": "pilot", "BUZZ_PILOT_CHANNEL": "not-a-uuid", "BUZZ_KEY_CLAUDE_PILOT": CLAUDE_KEY}, "BUZZ_PILOT_CHANNEL")):
            with self.assertRaises(ValueError) as caught:
                pilot_community.wire(env, relays([]), nip98.secret_from)
            self.assertEqual(str(caught.exception), name)

    def test_the_pilot_address_is_fixed_and_loopback(self):
        self.assertEqual(relay_choice.PILOT_URL, "http://127.0.0.1:3100")
        self.assertTrue(relay_choice.pilot_url_ok("http://127.0.0.1:3100/"))
        self.assertFalse(relay_choice.pilot_url_ok("https://life-os-dev.communities.buzz.xyz"))


if __name__ == "__main__":
    unittest.main()
