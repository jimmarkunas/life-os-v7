"""Event-driven memory alerts: one deduplicated Agent Inbox item per outage or unknown caller, counts and codes only."""

import unittest

from lifeos.platform import memory_monitor as mm


class Clock:
    def __init__(self, t=1_795_800_000.0):
        self.t = t

    def __call__(self):
        return self.t


def monitor(clock=None):
    sent = []
    m = mm.Monitor(
        sent.append, clock=clock or Clock(), dispatch=lambda send, item, report: send(item), report=lambda r: None
    )
    return m, sent


class Outage(unittest.TestCase):
    def test_three_unavailable_in_a_row_send_exactly_one_alert_and_recovery_updates_the_same_item(self):
        m, sent = monitor()
        m.memory_outcome("memory_unavailable")
        m.memory_outcome("memory_unavailable")
        self.assertEqual(sent, [])
        m.memory_outcome("memory_unavailable")
        m.memory_outcome("memory_unavailable")  # still the same incident: no second alert
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0]["key"].startswith("memory-unavailable-"))
        self.assertIn("3 memory calls in a row failed", sent[0]["subject"])
        m.memory_outcome(None)
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[1]["key"], sent[0]["key"])  # the same item is updated, not a new one
        self.assertEqual((sent[1]["subject"], sent[1]["urgency"]), ("Shared memory recovered", "FYI"))
        self.assertIn("4 calls", sent[1]["details"])

    def test_a_success_resets_the_count_and_caller_errors_neither_count_nor_reset(self):
        m, sent = monitor()
        for reason in ("memory_unavailable", "memory_unavailable", None, "memory_unavailable", "memory_unavailable"):
            m.memory_outcome(reason)
        self.assertEqual(sent, [])
        m.memory_outcome("invalid_argument")
        m.memory_outcome("sensitive_refused")
        self.assertEqual(sent, [])
        m.memory_outcome("memory_unavailable")
        self.assertEqual(len(sent), 1)

    def test_a_success_with_no_open_incident_sends_nothing(self):
        m, sent = monitor()
        m.memory_outcome(None)
        self.assertEqual(sent, [])

    def test_the_alert_carries_no_identity_or_text_only_codes_and_ct_times(self):
        m, sent = monitor()
        for _ in range(3):
            m.memory_outcome("memory_unavailable")
        item = sent[0]
        self.assertEqual(set(item), {"key", "subject", "details", "urgency", "workspace"})
        self.assertIn(" CT", item["details"])
        self.assertNotIn("UTC", item["details"])


class UnknownCaller(unittest.TestCase):
    def test_three_refusals_in_an_hour_from_one_unknown_caller_send_one_alert_with_its_fingerprint_only(self):
        clock = Clock()
        m, sent = monitor(clock)
        for _ in range(3):
            clock.t += 60
            m.unknown_caller("cred:stranger", "invalid_credential")
        self.assertEqual(len(sent), 1)
        fp = mm.fingerprint("cred:stranger")
        self.assertIn(fp, sent[0]["subject"])
        self.assertNotIn("stranger", sent[0]["subject"] + sent[0]["details"])
        clock.t += 60
        m.unknown_caller("cred:stranger", "invalid_credential")
        self.assertEqual(len(sent), 1)  # one alert per caller and hour

    def test_spread_out_refusals_and_different_callers_do_not_add_up(self):
        clock = Clock()
        m, sent = monitor(clock)
        for _ in range(3):
            clock.t += 3000  # more than an hour from the first by the third
            m.unknown_caller("oauth:x", "user_not_authorized")
        m.unknown_caller("oauth:y", "user_not_authorized")
        m.unknown_caller("oauth:z", "user_not_authorized")
        self.assertEqual(sent, [])

    def test_a_failed_send_is_reported_and_never_raises(self):
        reports = []

        def boom(item):
            raise RuntimeError("inbox down")

        m = mm.Monitor(boom, clock=Clock(), dispatch=mm._background, report=reports.append)
        for _ in range(3):
            m.memory_outcome("memory_unavailable")
        import time

        for _ in range(50):
            if reports:
                break
            time.sleep(0.01)
        self.assertEqual(reports[0]["outcome"], "send_failed")


if __name__ == "__main__":
    unittest.main()
