"""LIFE-249 (BUZZ-12 / BUZZ-17): universal identity-based Buzz @mentions, proved against a MOCKED relay with synthetic keys.
A signed recipient `p` tag is addressability in Buzz history. It never wakes a chat; nothing here proves a native agent responded."""
import hashlib, json, unittest

from lifeos import buzz_mcp, buzz_nip98 as nip98
from lifeos.buzz_relay import BuzzError, Relay
from tests.buzz import test_remote_mcp as remote
from tests.buzz.test_bridge import CHAN, KEY_A, KEY_B, OTHER, URLS, FakeRelay, call, verify

KEY_CODE, KEY_CODEX, KEY_CODE_B = bytes([31]) * 32, bytes([32]) * 32, bytes([33]) * 32
PK_CODE, PK_CODEX, PK_CODE_B = (nip98.pubkey(k) for k in (KEY_CODE, KEY_CODEX, KEY_CODE_B))
ALIASES = {("life-os", "claude-code"): PK_CODE, ("life-os", "codex"): PK_CODEX, ("bytalos", "claude-code"): PK_CODE_B}
ACTOR = "key:" + nip98.pubkey(KEY_A)[:16]
POST = dict(community="life-os", channel=CHAN, text="synthetic addressed hello", idempotency_key="m1")


KEY_ADMIN = bytes([40]) * 32
ROSTERS = {CHAN: [PK_CODE, PK_CODEX], OTHER: [PK_CODE_B]}   # the channels' current member lists (kind 39002)


def setup(aliases=ALIASES, may_address=True, rosters=ROSTERS):
    fake = FakeRelay()
    fake.events += [nip98.event(KEY_ADMIN, 39002, [["d", chan]] + [["p", pk] for pk in members], "") for chan, members in rosters.items()]
    return fake, Relay(URLS, {"life-os": KEY_A, "bytalos": KEY_B}, [("life-os", CHAN), ("bytalos", OTHER)], fake, recipients=aliases, may_address=may_address)


def kind9(fake):
    return [e for e in fake.events if e["kind"] == 9]


class Addressing(unittest.TestCase):
    def test_an_addressed_message_carries_a_signed_recipient_tag_and_reads_back_with_its_original_author(self):
        fake, relay = setup()
        out = relay.post_message(**POST, recipient="Claude-Code")
        (ev,) = kind9(fake)
        self.assertEqual((out["status"], out["event_id"], out["recipient"], out["actor"]), ("sent_not_read_back", ev["id"], "key:" + PK_CODE[:16], ACTOR))
        self.assertIn(["p", PK_CODE], ev["tags"])
        self.assertTrue(verify(ev["pubkey"], bytes.fromhex(ev["id"]), ev["sig"]))
        (seen,) = relay.get_messages("life-os", CHAN)
        self.assertEqual((seen["event_id"], seen["pubkey"], seen["actor"], seen["recipients"]), (ev["id"], nip98.pubkey(KEY_A), ACTOR, [PK_CODE]))

    def test_an_unaddressed_message_is_unchanged(self):
        fake, relay = setup()
        out = relay.post_message(**POST)
        (ev,) = kind9(fake)
        self.assertNotIn("recipient", out)
        self.assertFalse([t for t in ev["tags"] if t[0] == "p"])
        old = hashlib.sha256(f"life-os|{CHAN}|{ACTOR}|m1|{POST['text']}".encode()).hexdigest()[:32]   # the pre-LIFE-249 digest, so existing retries still dedupe
        self.assertIn(["i", old], ev["tags"])

    def test_aliases_are_case_insensitive_and_one_alias_resolves_per_community(self):
        fake, relay = setup()
        relay.post_message(**POST, recipient="CODEX")
        relay.post_message("bytalos", OTHER, "synthetic", "m2", recipient="claude-code")
        self.assertEqual([[t[1] for t in e["tags"] if t[0] == "p"] for e in kind9(fake)], [[PK_CODEX], [PK_CODE_B]])   # the Bytalos key, never the LIFE OS one


class Refusals(unittest.TestCase):
    def refused(self, relay, fake, code, **over):
        with self.assertRaises(BuzzError) as c:
            relay.post_message(**{**POST, **over})
        self.assertEqual(c.exception.code, code)
        self.assertEqual(kind9(fake), [])
        self.assertFalse([u for u, _ in fake.calls if u.endswith("/events")])   # nothing was ever sent

    def test_invalid_and_unknown_recipients_never_publish(self):
        fake, relay = setup()
        for bad in ("a b", "", "x" * 40, PK_CODE, 123, ["Codex"]):   # malformed, too long, or an arbitrary public key
            self.refused(relay, fake, "invalid_argument", recipient=bad)
        for unknown in ("Council-Architect", "Nobody", "ChatGPT-Chat"):   # well formed but not approved for this community
            self.refused(relay, fake, "recipient_not_authorized", recipient=unknown)

    def test_addressing_is_off_unless_the_sender_is_allowed_and_never_self_directed(self):
        fake, relay = setup(may_address=False)
        self.refused(relay, fake, "recipient_not_authorized", recipient="Claude-Code")
        fake, relay = setup(aliases={("life-os", "claude-chat"): nip98.pubkey(KEY_A)})
        self.refused(relay, fake, "recipient_not_authorized", recipient="Claude-Chat")

    def test_cross_community_and_revoked_recipients_are_refused(self):
        fake, relay = setup(aliases={("life-os", "codex"): PK_CODEX})
        self.refused(relay, fake, "recipient_not_authorized", community="bytalos", channel=OTHER, recipient="Codex")
        fake, relay = setup(aliases={k: v for k, v in ALIASES.items() if k[1] != "codex"})   # revoked: removed from the approved table
        self.refused(relay, fake, "recipient_not_authorized", recipient="Codex")

    def test_a_community_profile_without_channel_membership_publishes_nothing(self):
        profile = nip98.event(KEY_CODE, 0, [], json.dumps({"name": "claude-code", "display_name": "Claude Code"}))   # the identity exists in the community...
        fake, relay = setup(rosters={CHAN: [PK_CODEX], OTHER: [PK_CODE]})   # ...but is absent from THIS channel (a member only of another one)
        fake.events.append(profile)
        self.refused(relay, fake, "recipient_not_channel_member", recipient="Claude-Code")
        self.assertEqual(relay.post_message(**POST, recipient="Codex")["status"], "sent_not_read_back")   # the member of this channel still works
        fake, relay = setup(rosters={})   # no member list at all fails closed too
        self.refused(relay, fake, "recipient_not_channel_member", recipient="Codex")

    def test_an_unreachable_relay_fails_closed_before_any_event(self):
        fake, relay = setup()
        fake.fail_query = True
        self.refused(relay, fake, "precheck_unavailable", recipient="Claude-Code")


class Dedupe(unittest.TestCase):
    def test_retries_dedupe_and_cannot_retarget_even_in_a_fresh_process(self):
        fake, relay = setup()
        first = relay.post_message(**POST, recipient="Claude-Code")
        again = relay.post_message(**POST, recipient="Claude-Code")
        self.assertEqual((again["status"], again["event_id"]), ("already_delivered", first["event_id"]))
        for who in (relay, Relay(URLS, {"life-os": KEY_A}, [("life-os", CHAN)], fake, recipients=ALIASES, may_address=True)):   # same process and a restart
            for other in ("Codex", None):
                with self.assertRaises(BuzzError) as c:
                    who.post_message(**POST, recipient=other)
                self.assertEqual(c.exception.code, "idempotency_conflict")
        self.assertEqual(len(kind9(fake)), 1)

    def test_a_lost_acknowledgment_retries_to_the_same_single_addressed_event(self):
        fake, relay = setup()
        fake.fail_events = "after"
        with self.assertRaises(BuzzError) as c:
            relay.post_message(**POST, recipient="Codex")
        self.assertEqual(c.exception.code, "delivery_unknown")
        fake.fail_events = None
        self.assertEqual(relay.post_message(**POST, recipient="Codex")["status"], "already_delivered")
        self.assertEqual([[t[1] for t in e["tags"] if t[0] == "p"] for e in kind9(fake)], [[PK_CODEX]])


class Pending(unittest.TestCase):
    def test_an_inactive_identity_finds_its_addressed_messages_later_without_anything_being_acknowledged(self):
        fake, sender = setup()
        sent = sender.post_message(**POST, recipient="Claude-Code")
        sender.post_message(**{**POST, "idempotency_key": "m2", "text": "for someone else"}, recipient="Codex")
        sender.post_message(**{**POST, "idempotency_key": "m3", "text": "unaddressed"})
        receiver = Relay(URLS, {"life-os": KEY_CODE}, (), fake)   # the addressed identity, read-only, long after the post
        before = len(fake.events)
        first, second = receiver.get_addressed("life-os"), receiver.get_addressed("life-os", CHAN)
        self.assertEqual([(m["event_id"], m["actor"], m["recipients"]) for m in first["messages"]], [(sent["event_id"], ACTOR, [PK_CODE])])
        self.assertEqual((first, first["next_before"]), (second, None))   # fewer than a page: nothing older remains
        self.assertEqual(len(fake.events), before)   # reading wrote nothing: no inbox, no acknowledgment
        self.assertEqual(receiver.get_addressed("life-os", OTHER)["messages"], [])
        with self.assertRaises(BuzzError):
            receiver.get_addressed("life-os", "not-a-uuid")
        with self.assertRaises(BuzzError) as c:
            receiver.get_addressed("bytalos")
        self.assertEqual(c.exception.code, "key_not_configured")


class Paging(unittest.TestCase):
    def test_older_pending_messages_are_reachable_page_by_page_even_across_a_shared_second(self):
        fake, _ = setup()
        sent = [nip98.event(KEY_A, 9, [["h", CHAN], ["p", PK_CODE]], f"synthetic {i}", created_at=2000 - i // 2) for i in range(120)]   # pairs share a second
        fake.events += sent + [nip98.event(KEY_A, 9, [["h", CHAN], ["p", PK_CODEX]], "someone else", created_at=2001)]
        receiver = Relay(URLS, {"life-os": KEY_CODE}, (), fake)
        seen, before, pages = [], None, 0
        while True:
            page = receiver.get_addressed("life-os", limit=49, before=before)   # 49 splits a shared second across the page boundary
            seen += [m["event_id"] for m in page["messages"]]
            pages += 1
            if page["next_before"] is None:
                break
            before = page["next_before"]
            self.assertLess(pages, 10)
        self.assertEqual(set(seen), {e["id"] for e in sent})   # every one reached, none lost at a boundary
        self.assertGreater(len(seen), len(set(seen)))   # the boundary second repeats: callers dedupe by event id
        for bad in (0, -1, "5", True, 1.5):
            with self.assertRaises(BuzzError):
                receiver.get_addressed("life-os", before=bad)


class Server(unittest.TestCase):
    def test_the_tool_offers_only_approved_names_and_refuses_a_public_key(self):
        _, relay = setup()
        tools = {t["name"]: t for t in buzz_mcp.rpc(relay, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
        self.assertEqual(tools["buzz_post_message"]["inputSchema"]["properties"]["recipient"]["enum"], list(buzz_mcp.ALIASES))
        self.assertNotIn("recipient", tools["buzz_post_message"]["inputSchema"]["required"])
        self.assertTrue(tools["buzz_get_addressed"]["annotations"]["readOnlyHint"])
        self.assertIn("before", tools["buzz_get_addressed"]["inputSchema"]["properties"])
        err, out = call(relay, "buzz_post_message", **POST, recipient=PK_CODE)
        self.assertEqual((err, out["error"]), (True, "invalid_argument"))
        err, out = call(relay, "buzz_post_message", **POST, recipient="Codex")
        self.assertEqual((err, out["status"], out["recipient"]), (False, "sent_not_read_back", "key:" + PK_CODEX[:16]))

    def test_configuration_is_off_by_default_and_rejects_anything_unapproved(self):
        env = remote.Config.ENV
        self.assertEqual({r.may_address for r in buzz_mcp.from_env(env)[0].values()}, {False})
        relays = buzz_mcp.from_env({**env, "BUZZ_RECIPIENTS": f"Claude-Code:life-os:{PK_CODE}, Codex:bytalos:{PK_CODEX}", "BUZZ_MENTION_SENDERS": "claude"})[0]
        self.assertEqual((relays["claude"].may_address, relays["chatgpt"].may_address), (True, False))
        self.assertEqual(relays["chatgpt"].recipients, {("life-os", "claude-code"): PK_CODE, ("bytalos", "codex"): PK_CODEX})
        for bad in ({"BUZZ_RECIPIENTS": f"Bad Alias!:life-os:{PK_CODE}"}, {"BUZZ_RECIPIENTS": f"Codex:elsewhere:{PK_CODE}"}, {"BUZZ_RECIPIENTS": "Codex:life-os:nothex"},
                    {"BUZZ_RECIPIENTS": f"Codex:life-os:{PK_CODE},codex:life-os:{PK_CODEX}"}, {"BUZZ_RECIPIENTS": f"Codex:life-os:{PK_CODE}:x"}, {"BUZZ_MENTION_SENDERS": "admin"}):
            with self.assertRaises(ValueError):
                buzz_mcp.from_env({**env, **bad})


if __name__ == "__main__":
    unittest.main()
