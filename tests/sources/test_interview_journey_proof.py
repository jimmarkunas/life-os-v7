"""The LIFE-78 journey proof module, run exactly as the manual domains dispatch runs it, on the production-shaped Notion fake."""
import json
import unittest
from datetime import datetime
from lifeos.interview import notion
from lifeos.interview.ui_probe import QA_PARENT
from lifeos.sources import interview_evidence as ev, interview_journey_proof as proof
from tests.interview.support import ENV, page
from tests.sources.test_interview_journey import NotionLike

NOW = datetime(2026, 10, 22, 16, tzinfo=ev.TZ)


def qa_workspace():
    client = NotionLike()
    client.pages[QA_PARENT] = page("QA — Interview OS / Opportunity Parent")
    client.blocks[QA_PARENT] = []
    return client


class JourneyProof(unittest.TestCase):
    def test_the_whole_journey_passes_on_a_fresh_sandbox_and_nothing_outside_it_is_written(self):
        client = qa_workspace()
        real_pipeline = [dict(b) for b in client.blocks["root"]]
        counts = proof.run({**ENV, "INTERVIEW_JOURNEY_PROOF": "QA"}, False, now=NOW, client=client)
        self.assertEqual(counts["failed"], 0, json.dumps(counts, indent=1))
        self.assertTrue(all(s["pass"] for s in counts["steps"]))
        self.assertEqual(counts["checks"], {"pipeline_complete": True, "opportunities": 2, "ledger_rows": 2, "exampleco_rounds": 1, "exampleco_round_dated": True,
                                            "exampleco_round_cancelled": True, "samplecorp_rounds": 0, "pass": True})
        self.assertEqual(client.blocks["root"], real_pipeline)                         # the real Hiring Pipeline (here "root") is never touched
        (container,) = [b for b in client.blocks[QA_PARENT] if b.get("type") == "child_page"]
        self.assertTrue(container["child_page"]["title"].endswith("(synthetic)"))
        posts = {p["parent"].get("page_id") or p["parent"]["data_source_id"] for p in client.posts}
        self.assertNotIn("root", posts)                                                # every page this proof created sits inside its sandbox
        self.assertIn(client.sandbox_source, posts)                                        # LIFE-361: the pursuits are rows of the sandbox's own Ledger
        self.assertNotIn("ds-synthetic", posts)                                        # never the configured (real) Ledger
        self.assertEqual(len(client.rows), 2)                                          # the two leads' own rows, real sandbox rows; the Interview step made no second row
        (decision,) = [s for s in counts["steps"] if s["step"].startswith("D ")]        # LIFE-353 GWT-DW-1/2 on the sandbox row, each repeated as a no-op
        self.assertEqual((decision["pass"], decision["writes"], decision["why"]), (True, 2, {"decision_already_recorded": 2, "decision_recorded": 2}))
        row = next(r for r in client.rows.values() if client._value(r["Company"]) == "ExampleCo")
        self.assertEqual((client._value(row["Hiring Decision"]), len(client._value(row["Hiring Decision History"]).splitlines())), (None, 2))
        self.assertFalse([s["step"] for s in counts["steps"] if "network_unreadable" in s["why"]])     # every row page the readers open is a real page

    def test_counts_only_never_names_ids_links_or_people(self):
        counts = proof.run({**ENV, "INTERVIEW_JOURNEY_PROOF": "QA"}, False, now=NOW, client=qa_workspace())
        text = json.dumps(counts)
        for private in ("example.test", "Pat Example", "created-", QA_PARENT, "synthetic-ledger-"):
            self.assertNotIn(private, text)

    def test_it_refuses_without_the_typed_confirmation_or_with_actually_write_ticked(self):
        client = qa_workspace()
        self.assertEqual(proof.run({**ENV, "INTERVIEW_JOURNEY_PROOF": "yes"}, False, client=client)["code"], "proof_confirm_missing")
        self.assertEqual(proof.run({**ENV, "INTERVIEW_JOURNEY_PROOF": "QA"}, True, client=client)["code"], "proof_requires_dry_dispatch")
        self.assertEqual(client.posts, [])

    def test_the_normal_interview_step_runs_the_proof_only_when_asked(self):
        client = qa_workspace()
        counts = ev.run(20, False, environ={**ENV, "INTERVIEW_JOURNEY_PROOF": "QA"}, client=client)
        self.assertEqual((counts["proof"], counts["failed"]), ("journey", 0))


if __name__ == "__main__":
    unittest.main()
