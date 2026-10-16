"""Fit v2 offline end-to-end proof (LIFE-41 slice 1): the real request builder, a simulated model, the acceptance envelope and the production admission code (stage.score_rows, so the Newsletter, US Remote
and Scale-Up lane rules) together. The model is a stand-in that returns canned answers and records what it was sent; the target scores are PROVISIONAL (J, K and L await Jim's judgment), not model output
and not approved ground truth. Nothing here calls a model, the network or the database, and the static path it is compared with is the unmodified production path."""
import dataclasses
import json
from datetime import timedelta
import unittest
from unittest import mock

from lifeos.jobs.fit import judge, stage
from tests.jobs.fit.test_judge import JOBS, PROFILE, PROVISIONAL, TODAY, answer

LANES = {"Newsletter": ("Remote - US", None, "US Remote"), "Scale-Up": ("London, UK", "Scale-up:POSITIVE", "Scale-Up")}
# case -> (static admission, v2 admission), identical in every lane because the lane rules read the same Fit number
EXPECTED = {
    "A clear keyword match": ("ADMIT", "ADMIT"), "B paraphrased delivery lead": ("ADMIT", "ADMIT"), "C adjacent implementation role": ("ADMIT", "ADMIT"),
    "D junior title, keywords stuffed": ("EXCLUDE", "EXCLUDE"), "E hands-on engineering mismatch": ("EXCLUDE", "EXCLUDE"), "F title only": ("REVIEW", "REVIEW"),
    "G senior product, integration platform": ("ADMIT", "ADMIT"), "H excluded domain": ("EXCLUDE", "EXCLUDE"), "I program manager, unrelated domain": ("EXCLUDE", "EXCLUDE"),
    "J exec stretch": ("ADMIT", "EXCLUDE"), "K paraphrase, no profile vocabulary": ("REVIEW", "ADMIT"), "L solutions architect, adjacent craft": ("ADMIT", "REVIEW"),
}


def row(title, company, text, lane, location, route):
    return (1, title, company, text, "fp", lane, location, None, TODAY - timedelta(days=1), TODAY, route, None)


class Model:
    """A simulated capability: records every request it receives and returns a canned answer."""
    def __init__(self, reply):
        self.reply, self.seen = reply, []

    def __call__(self, sent):
        self.seen.append(sent)
        return self.reply


def run(name, lane, reply=None, title=None, text=None, location=None):
    """-> (static score_rows tuple, v2 score_rows tuple or None, the Judgment or None, the model). The v2 side feeds the accepted Fit into the unmodified production admission code."""
    place, route, _ = LANES[lane]
    case_title, company, case_text = JOBS[name]
    title, text, location = title or case_title, text or case_text, location or place
    r = row(title, company, text, lane, location, route)
    static = stage.score_rows([r], PROFILE, TODAY)[0]
    sent = judge.request(title, text, {"years": 15}, location)
    model = Model(answer(PROVISIONAL[name][1]) if reply is None else reply)
    raw = model(sent) if sent is not None else None                    # the egress boundary: nothing is asked when there is nothing safe to send
    judgment = judge.accept(raw, static[2], title, sent)
    if judgment is None:
        return static, None, None, model
    with mock.patch.object(stage, "evaluate", return_value=dataclasses.replace(static[2], score=judgment.score, decision=judgment.decision)):
        return static, stage.score_rows([r], PROFILE, TODAY)[0], judgment, model


class EndToEnd(unittest.TestCase):
    def test_every_representative_case_in_the_newsletter_and_scale_up_lanes(self):
        self.assertEqual(set(EXPECTED), set(JOBS))
        for lane, (_, _, visible) in LANES.items():
            for name, (static_status, v2_status) in EXPECTED.items():
                static, v2, judgment, model = run(name, lane)
                self.assertEqual(static[3].status, static_status, (lane, name))          # the unmodified static path is unchanged
                self.assertEqual(v2[3].status, v2_status, (lane, name))
                self.assertEqual((static[4], v2[4]), (visible, visible), (lane, name))   # the lane the job lands in does not move
                self.assertEqual(len(model.seen), 0 if name == "F title only" else 1, name)

    def test_the_decisions_that_differ_are_exactly_j_k_and_l_and_the_unscorable_stays_review(self):
        flips = sorted(n for n, (a, b) in EXPECTED.items() if a != b)
        self.assertEqual(flips, ["J exec stretch", "K paraphrase, no profile vocabulary", "L solutions architect, adjacent craft"])
        static, v2, judgment, model = run("F title only", "Newsletter")
        self.assertEqual((judgment.score, judgment.decision, v2[3].reason), (None, "Unscorable", "Fit unscorable"))      # never a percentage, never a model call
        self.assertEqual(model.seen, [])

    def test_the_gate_and_the_caps_decide_whatever_the_model_says(self):
        for name, high in (("H excluded domain", 99), ("E hands-on engineering mismatch", 99), ("D junior title, keywords stuffed", 99)):
            static, v2, judgment, _ = run(name, "Newsletter", answer(high))
            self.assertEqual(v2[3].status, "EXCLUDE", name)
        self.assertEqual(judgment.score, 55)                                           # the level cap, code-owned

    def test_an_unusable_answer_leaves_the_static_scorer_authoritative_before_cutover(self):
        for bad in ("", "not json", answer(101), json.dumps({"score": 80}), answer(80, confidence="certain")):
            static, v2, judgment, model = run("K paraphrase, no profile vocabulary", "Newsletter", bad)
            self.assertEqual((v2, judgment, len(model.seen)), (None, None, 1), repr(bad))
            self.assertEqual(static[3].status, "REVIEW")                                  # the caller keeps the static decision pre-cutover; after cutover this needs an approved path

    def test_the_floor_72_and_the_review_band_apply_to_the_accepted_fit(self):
        for score, status in ((72, "ADMIT"), (71, "REVIEW"), (60, "REVIEW"), (59, "EXCLUDE")):
            self.assertEqual(run("K paraphrase, no profile vocabulary", "Newsletter", answer(score))[1][3].status, status, score)

    def test_what_the_model_sees_carries_no_place_pay_or_work_mode_from_the_structured_fields_or_the_text(self):
        text = JOBS["B paraphrased delivery lead"][2].replace("• Run the weekly steering forum and publish decisions",
                                                              "• Run the weekly steering forum and publish decisions\n• Partner with the Austin hub; Salary range $150,000 to $180,000; hybrid role, Texas office")
        static, v2, judgment, model = run("B paraphrased delivery lead", "Newsletter", title="Delivery Lead, Austin", text=text, location="Austin, Texas")
        payload = json.dumps(model.seen[0])
        for leaked in ("Austin", "Texas", "$150", "hybrid", "Salary"):
            self.assertNotIn(leaked, payload, leaked)
        self.assertIn("steering forum", payload)
        self.assertEqual(model.seen[0]["title"], "Delivery Lead")

    def test_an_ambiguous_title_sends_nothing_and_the_posting_is_unscorable(self):
        static, v2, judgment, model = run("B paraphrased delivery lead", "Newsletter", title="Remote - Chicago")
        self.assertEqual((model.seen, judgment.decision, judgment.score), ([], "Unscorable", None))
        self.assertEqual(v2[3].reason, "Fit unscorable")


if __name__ == "__main__":
    unittest.main()
