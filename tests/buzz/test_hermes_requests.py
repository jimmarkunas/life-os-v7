"""LIFE-307 / LIFE-293: the `hermes` stage, proved against a MOCKED Jira and a MOCKED Buzz relay with synthetic keys and invented issue keys.
Nothing here touches a network. A passing run proves the planning, idempotency and refusal logic, not that a live Hermes answered."""
import unittest

from lifeos.buzz import nip98
from lifeos.buzz.relay import Relay
from lifeos.platform.jira import JiraError
from lifeos import hermes_requests as hr
from tests.buzz.test_bridge import CHAN, KEY_A, URLS, FakeRelay

KEY_HERMES, KEY_ADMIN = bytes([21]) * 32, bytes([40]) * 32
PK_HERMES = nip98.pubkey(KEY_HERMES)


class FakeJira:
    def __init__(self, keys=(), fail=False):
        self.keys, self.fail, self.calls = list(keys), fail, 0

    def get(self, path, params=None):
        self.calls += 1
        if self.fail:
            raise JiraError("JIRA_NETWORK")
        return {"issues": [{"key": k} for k in self.keys], "isLast": True}


def setup(live=True, may_address=True):
    fake = FakeRelay()
    fake.check_auth = False                                                        # the signature checks are not what this file proves
    fake.events.append(nip98.event(KEY_ADMIN, 39002, [["d", CHAN], ["p", PK_HERMES]], ""))
    relay = Relay(URLS, {"life-os": KEY_A}, [("life-os", CHAN)] if live else [], fake, recipients={("life-os", "hermes"): PK_HERMES}, may_address=may_address)
    return fake, relay


def writes(fake):
    return [u for u, _ in fake.calls if u.endswith("/events")]


def kind9(fake):
    return [e for e in fake.events if e["kind"] == 9]


def run(fake_jira, relay, live, limit=40):
    return hr.run(limit, live, jira=fake_jira, relay=relay, channel=CHAN)


class Planning(unittest.TestCase):
    def test_a_dry_run_counts_what_is_new_and_writes_nothing(self):
        fake, relay = setup()
        relay.post_message("life-os", CHAN, "seed", "seed1", recipient="hermes", etype="handoff_request", case=hr.case_of("LIFE-1"))   # one request already exists
        before = len(writes(fake))
        counts = run(FakeJira(["LIFE-1", "LIFE-2", "LIFE-3"]), relay, live=False)
        self.assertEqual((counts["eligible"], counts["already"], counts["planned"], counts["posted"]), (3, 1, 2, 0))
        self.assertEqual(len(writes(fake)), before)

    def test_no_eligible_issue_posts_nothing_and_never_reads_buzz(self):
        fake, relay = setup()
        counts = run(FakeJira([]), relay, live=True)
        self.assertEqual((counts["eligible"], counts["planned"], counts["posted"]), (0, 0, 0))
        self.assertEqual(fake.calls, [])

    def test_keys_that_are_not_life_issues_are_counted_and_ignored(self):
        fake, relay = setup()
        counts = run(FakeJira(["LIFE-7", "ABC-1", "LIFE-x", "life-9"]), relay, live=False)
        self.assertEqual((counts["eligible"], counts["invalid"], counts["planned"]), (1, 3, 1))


class Posting(unittest.TestCase):
    def test_live_posts_once_per_issue_and_a_second_run_posts_nothing(self):
        fake, relay = setup()
        jira = FakeJira(["LIFE-10", "LIFE-2"])
        first = run(jira, relay, live=True)
        self.assertEqual((first["posted"], first["verified"], first["failed"]), (2, 2, 0))
        sent = kind9(fake)
        self.assertEqual(len(sent), 2)
        for ev in sent:
            self.assertIn(["p", PK_HERMES], ev["tags"])
            self.assertIn(["t", "handoff_request"], ev["tags"])
        count = len(writes(fake))
        second = run(jira, relay, live=True)
        self.assertEqual((second["already"], second["planned"], second["posted"]), (2, 0, 0))
        self.assertEqual(len(writes(fake)), count)

    def test_the_request_text_is_a_fixed_template_with_the_issue_key_only(self):
        fake, relay = setup()
        run(FakeJira(["LIFE-5"]), relay, live=True)
        (ev,) = kind9(fake)
        self.assertEqual(ev["content"], hr.TEXT.format(case="LIFE-5-AGENTGO", key="LIFE-5", status="Agent Go"))
        self.assertIn(["c", "LIFE-5-AGENTGO"], ev["tags"])

    def test_the_limit_defers_the_rest(self):
        fake, relay = setup()
        counts = run(FakeJira(["LIFE-1", "LIFE-2", "LIFE-3"]), relay, live=True, limit=2)
        self.assertEqual((counts["posted"], counts["deferred"]), (2, 1))

    def test_a_replayed_post_is_already_delivered(self):
        fake, relay = setup()
        relay.post_message("life-os", CHAN, hr.TEXT.format(case="LIFE-4-AGENTGO", key="LIFE-4", status="Agent Go"), hr.idempotency_of("LIFE-4"), recipient="hermes", etype="handoff_request", case="LIFE-4-AGENTGO")
        again = relay.post_message("life-os", CHAN, hr.TEXT.format(case="LIFE-4-AGENTGO", key="LIFE-4", status="Agent Go"), hr.idempotency_of("LIFE-4"), recipient="hermes", etype="handoff_request", case="LIFE-4-AGENTGO")
        self.assertEqual(again["status"], "already_delivered")


class Refusals(unittest.TestCase):
    def test_a_refused_recipient_stops_with_a_fixed_code_and_posts_nothing(self):
        fake, relay = setup(may_address=False)
        with self.assertRaises(hr.HermesError) as c:
            run(FakeJira(["LIFE-1", "LIFE-2"]), relay, live=True)
        self.assertEqual(str(c.exception), "HERMES_BUZZ_RECIPIENT_NOT_AUTHORIZED")
        self.assertEqual(kind9(fake), [])

    def test_a_dry_run_relay_has_no_write_permission(self):
        fake, relay = setup(live=False)
        with self.assertRaises(hr.HermesError) as c:
            run(FakeJira(["LIFE-1"]), relay, live=True)
        self.assertEqual(str(c.exception), "HERMES_BUZZ_WRITE_DISABLED")
        self.assertEqual(kind9(fake), [])

    def test_an_unacknowledged_post_is_a_fixed_code_not_a_success(self):
        fake, relay = setup()
        fake.drop_ack = True
        with self.assertRaises(hr.HermesError) as c:
            run(FakeJira(["LIFE-1"]), relay, live=True)
        self.assertEqual(str(c.exception), "HERMES_BUZZ_DELIVERY_UNKNOWN")

    def test_an_unreadable_relay_plans_nothing(self):
        fake, relay = setup()
        fake.fail_query = True
        with self.assertRaises(hr.HermesError) as c:
            run(FakeJira(["LIFE-1"]), relay, live=True)
        self.assertEqual(str(c.exception), "HERMES_BUZZ_RELAY_ERROR")
        self.assertEqual(writes(fake), [])

    def test_a_jira_failure_is_its_own_fixed_code_and_never_reaches_buzz(self):
        fake, relay = setup()
        with self.assertRaises(JiraError) as c:
            run(FakeJira(fail=True), relay, live=True)
        self.assertEqual(str(c.exception), "JIRA_NETWORK")
        self.assertEqual(fake.calls, [])


class Config(unittest.TestCase):
    ENV = {"BUZZ_URL_LIFE_OS": "https://example-a.communities.buzz.xyz", "BUZZ_STAGE_KEY": "07" * 32, "BUZZ_STAGE_CHANNEL": CHAN, "BUZZ_STAGE_HERMES_PUBKEY": PK_HERMES}

    def test_missing_settings_are_named_without_values(self):
        with self.assertRaises(hr.HermesError) as c:
            hr.relay_from_env({"BUZZ_URL_LIFE_OS": self.ENV["BUZZ_URL_LIFE_OS"]}, live=True)
        self.assertEqual(str(c.exception), "HERMES_CONFIG_MISSING:BUZZ_STAGE_KEY,BUZZ_STAGE_CHANNEL,BUZZ_STAGE_HERMES_PUBKEY")

    def test_invalid_settings_are_one_fixed_code(self):
        for name, bad in (("BUZZ_URL_LIFE_OS", "http://example.com"), ("BUZZ_STAGE_CHANNEL", "not-a-uuid"), ("BUZZ_STAGE_HERMES_PUBKEY", "zz"), ("BUZZ_STAGE_KEY", "nope")):
            with self.assertRaises(hr.HermesError) as c:
                hr.relay_from_env({**self.ENV, name: bad}, live=True)
            self.assertEqual(str(c.exception), "HERMES_CONFIG_INVALID")

    def test_only_a_live_run_gets_write_permission(self):
        dry, _ = hr.relay_from_env(self.ENV, live=False)
        live, channel = hr.relay_from_env(self.ENV, live=True)
        self.assertEqual((dry.writes, live.writes, channel), (set(), {("life-os", CHAN)}, CHAN))
        self.assertTrue(live.may_address)

    def test_the_stage_is_registered_with_the_runner(self):
        from lifeos import run as runner
        self.assertEqual(runner.STAGES["hermes"].target, ("lifeos.hermes_requests", "run"))


if __name__ == "__main__":
    unittest.main()
