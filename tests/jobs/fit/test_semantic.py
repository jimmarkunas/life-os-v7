"""The semantic layer with a fake embedder (controlled cosines): it only ranks, the class comes from the profile's own
capability, below LOW is no match, and it never changes the real score (shadow)."""
from datetime import date
import math
import sys
import unittest
from unittest import mock

from lifeos.jobs.fit import semantic
from lifeos.jobs.fit.profile import Profile
from lifeos.jobs.fit.score import evaluate

PROFILE = Profile({"years": 10, "capabilities": [
    {"id": "risk", "label": "risk mgmt", "class": "direct", "terms": ["risk management"], "bucket": "skill"}]})
LINE = "Experience overseeing program exposure and issue tracking across teams"


def vec(cos):
    return [cos, math.sqrt(1 - cos * cos), 0.0]


def embedder(cos):
    """Profile phrases sit on the first axis; the posting line sits at the given cosine from it; anything else is orthogonal."""
    def embed(texts):
        return [vec(cos) if t == LINE else [1.0, 0.0, 0.0] if t in ("risk management", "risk mgmt") else [0.0, 0.0, 1.0]
                for t in texts]
    return embed


def jd():
    return "Requirements\n• " + LINE + "\n• Experience with stakeholder reporting\n" + "padding words here " * 40


class SemanticTests(unittest.TestCase):
    def test_band_edges_and_class_from_the_capability(self):
        m = semantic.Matcher(PROFILE, embedder(0.85))
        row, sim, cls = m.best_many([LINE])[0]
        self.assertEqual((row["id"], cls), ("risk", "direct"))                     # >= HIGH keeps the capability's class
        self.assertEqual(semantic.Matcher(PROFILE, embedder(0.76)).best_many([LINE])[0][2], "adjacent")   # one step weaker
        row, _, cls = semantic.Matcher(PROFILE, embedder(0.5)).best_many([LINE])[0]
        self.assertEqual((row, cls), (None, "unsupported"))                         # below LOW: fail closed

    def test_shadow_never_changes_the_real_score(self):
        base = evaluate("Program Manager", "B", jd(), PROFILE, date(2026, 10, 1))
        shadow = evaluate("Program Manager", "B", jd(), PROFILE, date(2026, 10, 1), semantic.Matcher(PROFILE, embedder(0.85)))
        self.assertEqual((shadow.score, shadow.line), (base.score, base.line))
        self.assertEqual(shadow.shadow_changes, 1)
        self.assertGreater(shadow.shadow_score, base.score)
        self.assertEqual(shadow.shadow_sims, [0.85])

    def test_no_match_means_no_change(self):
        shadow = evaluate("Program Manager", "B", jd(), PROFILE, date(2026, 10, 1), semantic.Matcher(PROFILE, embedder(0.5)))
        self.assertEqual((shadow.shadow_changes, shadow.shadow_score), (0, shadow.score))
        self.assertFalse(shadow.shadow_flip)

    def test_layer_is_off_without_the_model(self):
        with mock.patch.dict(sys.modules, {"fastembed": None}):
            self.assertIsNone(semantic.load_embedder({}))


if __name__ == "__main__":
    unittest.main()
