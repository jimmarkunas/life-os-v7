"""LIFE-78 M1/M2 synthetic exit (lifeos/sources/m1m2_proof.py), run exactly as the manual interview-proof dispatch runs it, on the production-shaped Notion fake."""
import json
import unittest
from datetime import datetime
from lifeos.sources import interview_evidence as ev, m1m2_proof as proof
from tests.interview.test_notion import ENV
from tests.sources.test_interview_journey_proof import qa_workspace

NOW = datetime(2026, 10, 23, 13, tzinfo=ev.TZ)


class M1M2Proof(unittest.TestCase):
    def test_every_assertion_passes_on_a_fresh_sandbox_and_nothing_outside_it_is_written(self):
        client = qa_workspace()
        real_pipeline = [dict(b) for b in client.blocks["root"]]
        counts = proof.run({**ENV, "INTERVIEW_JOURNEY_PROOF": "M1M2"}, False, now=NOW, client=client, reader=client)
        self.assertEqual(counts["proof"], "m1m2")
        self.assertEqual(counts["failed"], 0, json.dumps(counts["assertions"], indent=1))
        self.assertGreaterEqual(counts["passed"], 30)
        self.assertEqual(client.blocks["root"], real_pipeline)                                 # the real Hiring Pipeline (here "root") is never touched
        self.assertNotIn("root", {p["parent"]["page_id"] for p in client.posts})

    def test_counts_only_never_names_ids_links_or_people(self):
        client = qa_workspace()
        text = json.dumps(proof.run({**ENV, "INTERVIEW_JOURNEY_PROOF": "M1M2"}, False, now=NOW, client=client, reader=client))
        for private in ("example.test", "Riley", "Staffing Example", "created-", "5100"):
            self.assertNotIn(private, text)

    def test_the_interview_step_routes_m1m2_to_this_proof_only(self):
        client = qa_workspace()
        counts = ev.run(20, False, environ={**ENV, "INTERVIEW_JOURNEY_PROOF": "M1M2"}, client=client, now=NOW)
        self.assertEqual(counts["proof"], "m1m2")
        self.assertEqual(ev.run(20, False, environ={**ENV, "INTERVIEW_JOURNEY_PROOF": "QA"}, client=qa_workspace())["proof"], "journey")

    def test_it_refuses_without_its_own_confirmation_or_with_actually_write_ticked(self):
        client = qa_workspace()
        self.assertEqual(proof.run({**ENV, "INTERVIEW_JOURNEY_PROOF": "QA"}, False, client=client)["code"], "proof_confirm_missing")
        self.assertEqual(proof.run({**ENV, "INTERVIEW_JOURNEY_PROOF": "M1M2"}, True, client=client)["code"], "proof_requires_dry_dispatch")
        self.assertEqual(client.posts, [])


if __name__ == "__main__":
    unittest.main()
