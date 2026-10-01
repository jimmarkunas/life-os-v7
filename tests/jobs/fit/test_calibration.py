"""The eight accepted calibration fixtures, encoded exactly as frozen: title evidence + material requirement rows with
dimension, priority and evidence class. They are regression tests of the arithmetic, not production candidate truth.
Rows are never enriched or altered to make a result come out (see docs/FIT_MODEL.md)."""
from fractions import Fraction as F
import unittest

from lifeos.jobs.fit.score import Req, calculate

R, N = F(2), F(1)                      # REQUIRED / NORMAL priority weight
D, A, M, U = "direct", "adjacent", "method", "unsupported"
ROLE, FUNC, TECH, DELIV, ADV = "role", "functional", "technical", "delivery", "advantage"


def reqs(*rows):
    return [Req(dim, text, cls, weight) for text, dim, weight, cls in rows]


GPM_1 = (D, reqs(("Cross-functional program ownership", ROLE, N, D), ("Program planning", FUNC, N, D),
                 ("Stakeholder coordination", FUNC, N, D), ("Delivery governance", FUNC, N, D),
                 ("Dependency/risk management", FUNC, N, D), ("Executive reporting", FUNC, N, D),
                 ("Vendor/team orchestration", FUNC, N, D)))
GPM_2 = (D, reqs(("Program planning", FUNC, N, D), ("Budget/schedule ownership", FUNC, N, D),
                 ("Stakeholder management", FUNC, N, D), ("Risk/dependency management", FUNC, N, D),
                 ("Change/process coordination", FUNC, N, D), ("Delivery governance", FUNC, N, D),
                 ("Understand platform/integration dependencies sufficiently to coordinate delivery", TECH, N, A)))
STRONG_TPM = (D, reqs(("Must own enterprise technology programs with executive accountability", ROLE, R, D),
                      ("Must lead technical program planning, governance, risk and dependency management", FUNC, R, D),
                      ("Must lead platform modernization / systems integration across enterprise systems", TECH, R, D),
                      ("Must lead large cross-functional, multi-organization / multi-vendor delivery", DELIV, R, D),
                      ("Distressed-program recovery experience is a plus", ADV, N, D)))
TPM_PRODUCT = (D, reqs(("Must own enterprise program outcomes and executive accountability", ROLE, R, D),
                       ("Must lead technical program planning, governance, risks and dependencies", FUNC, R, D),
                       ("Own product roadmap and prioritization with product stakeholders", FUNC, N, D),
                       ("Must coordinate platform, API and integration dependencies", TECH, R, D),
                       ("Lead multi-organization cross-functional delivery", DELIV, N, D)))
TECH_PM = (D, reqs(("Own enterprise product scope and executive-stakeholder accountability", ROLE, N, D),
                   ("Must own roadmap, prioritization, requirements and delivery outcomes", FUNC, R, D),
                   ("Must operate across APIs, integration boundaries and platform architecture", TECH, R, A),
                   ("Lead cross-functional enterprise delivery", DELIV, N, D)))
SENIOR_PM = (A, reqs(("Must own senior/enterprise product scope with executive accountability", ROLE, R, D),
                     ("Must own product strategy, roadmap, prioritization and outcomes", FUNC, R, D),
                     ("Familiarity with enterprise platform/integration architecture", TECH, N, A),
                     ("Lead cross-functional enterprise product delivery", DELIV, N, D)))
SOLUTIONS_ARCHITECT = (M, reqs(("Must own senior client-facing solution scope and executive technical conversations", ROLE, R, D),
                               ("Lead client discovery and translate business needs into implementation direction", FUNC, N, D),
                               ("Must define solution architecture / integration boundaries across enterprise systems", TECH, R, A),
                               ("Coordinate complex multi-system enterprise implementation", DELIV, N, D)))
CLOUD_ENGINEER = (U, reqs(("Coordinate cross-functional stakeholders and delivery dependencies", FUNC, N, D),
                          ("Must build and operate cloud infrastructure hands-on", TECH, R, U),
                          ("Deliver across complex multi-system enterprise environments", DELIV, N, D)))


class CalibrationFixtures(unittest.TestCase):
    def check(self, fixture, final, uncapped=None, capped=False, maximum=None, contributions=None):
        title, rows = fixture
        calc = calculate(rows, title, capped=capped)
        self.assertEqual(calc.final, final)
        if uncapped is not None:
            self.assertEqual(calc.uncapped, uncapped)             # exact, not rounded
        if maximum is not None:
            self.assertEqual(calc.applicable_max, maximum)
        for dim, value in (contributions or {}).items():
            self.assertEqual(calc.contributions[dim], value, dim)
        return calc

    def test_gpm_1(self):
        c = self.check(GPM_1, 100, F(100), maximum=F(58), contributions={ROLE: F(50), FUNC: F(50)})
        self.assertNotIn(TECH, c.contributions)

    def test_gpm_2_exact(self):
        c = self.check(GPM_2, 91, F(20800, 229), maximum=F(229, 4),
                       contributions={ROLE: F(2900, 229), FUNC: F(11600, 229), TECH: F(6300, 229)})
        self.assertNotIn(DELIV, c.contributions)
        self.assertAlmostEqual(float(c.uncapped), 90.829694, places=6)

    def test_strong_senior_tpm(self):
        self.check(STRONG_TPM, 100, F(100), maximum=F(100),
                   contributions={ROLE: F(29), FUNC: F(29), TECH: F(21), DELIV: F(14), ADV: F(7)})

    def test_tpm_product_hybrid(self):
        self.check(TPM_PRODUCT, 100, F(100), maximum=F(93),
                   contributions={ROLE: F(2900, 93), FUNC: F(2900, 93), TECH: F(700, 31), DELIV: F(1400, 93)})

    def test_technical_product_manager(self):
        self.check(TECH_PM, 94, F(2925, 31), maximum=F(93),
                   contributions={ROLE: F(2900, 93), FUNC: F(2900, 93), TECH: F(525, 31), DELIV: F(1400, 93)})

    def test_senior_product_manager(self):
        self.check(SENIOR_PM, 92, F(34375, 372), maximum=F(93),
                   contributions={ROLE: F(3625, 124), FUNC: F(2900, 93), TECH: F(525, 31), DELIV: F(1400, 93)})

    def test_solutions_architect_is_not_a_hard_mismatch(self):
        self.check(SOLUTIONS_ARCHITECT, 91, F(8485, 93), maximum=F(93),
                   contributions={ROLE: F(870, 31), FUNC: F(2900, 93), TECH: F(525, 31), DELIV: F(1400, 93)})

    def test_cloud_infrastructure_engineer_is_capped_at_40(self):
        title, rows = CLOUD_ENGINEER
        c = self.check(CLOUD_ENGINEER, 40, F(3440, 57), capped=True, maximum=F(285, 4),
                       contributions={ROLE: F(0), FUNC: F(2320, 57), TECH: F(0), DELIV: F(1120, 57)})
        self.assertAlmostEqual(float(c.uncapped), 60.350877, places=6)
        self.assertEqual(calculate(rows, title).final, 60)        # without the cap it would pass the 40 line

    def test_arithmetic_boundaries_and_cap_ceiling(self):
        self.assertEqual(calculate(GPM_1[1], D, bonus=True).final, 100)        # never above 100
        self.assertEqual(calculate(CLOUD_ENGINEER[1], U, bonus=True, capped=True).final, 40)   # the cap wins after the bonus
        self.assertEqual(calculate([], D).final, 100)                          # title alone is never scored: evaluate() refuses it


if __name__ == "__main__":
    unittest.main()
