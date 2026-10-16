"""Fit v2 envelope (LIFE-41): the representative synthetic set, the static baseline beside a PROVISIONAL v2 outcome, and every way the capability's answer can be refused.
Invented employers and a synthetic profile only (D16). The capability answers and target scores here are provisional test inputs, not model output and not approved ground truth:
Jim has accepted none of them (cases J, K and L in particular await his judgment against real evidence). The envelope rules they exercise are the approved ones."""
from datetime import date
import json
import unittest

from lifeos.jobs.fit import judge
from lifeos.jobs.fit.score import evaluate
from tests.jobs.fit.test_fit import GOOD, PROFILE

TODAY = date(2026, 10, 1)
PAD = " Our teams value clear writing, steady delivery and honest status updates." * 6
JOBS = {}
JOBS["A clear keyword match"] = ("Technical Program Manager", "Northwind Commerce", GOOD)
JOBS["B paraphrased delivery lead"] = ("Delivery Lead, Platform Programs", "Contoso Retail", """About the role
You will shepherd our largest cross-team initiatives from kickoff to launch.

Responsibilities
• Own the plan, milestones and status reporting to the leadership team
• Coordinate engineering, vendors and business owners so work lands on time
• Surface risks early and keep dependencies moving across several groups
• Run the weekly steering forum and publish decisions

Requirements
• 10+ years leading complex multi-team technology initiatives
• Track record of shipping enterprise systems on schedule and budget
• Clear written and verbal communication with executives
""" + PAD)
JOBS["C adjacent implementation role"] = ("Implementation Manager, ERP Rollouts", "Fabrikam Systems", """About the role
Lead customer ERP rollouts end to end.

Responsibilities
• Run implementation projects for mid-market customers
• Configure and integrate the ERP with billing and payment systems
• Train customer administrators and hand over to support

Requirements
• 5+ years of implementation or consulting experience
• Hands-on ERP configuration experience
• Experience with payment providers
""" + PAD)
JOBS["D junior title, keywords stuffed"] = ("Data Entry Clerk", "Adventure Works", """About the role
Entry-level support role.

Responsibilities
• Enter NetSuite records and reconcile Stripe payment batches
• Assist the program management office with ERP report exports

Requirements
• 1+ years of ERP data entry
• Familiarity with NetSuite and payments
""" + PAD)
JOBS["E hands-on engineering mismatch"] = ("Staff Software Engineer, Payments", "Tailspin Pay", """About the role
Build our payment processing services.

Responsibilities
• Write production Java and Go services that process card payments
• Design database schemas, on-call for the payments platform
• Review code and mentor engineers

Requirements
• 8+ years of professional software engineering in Java or Go
• Deep experience with distributed systems and Kubernetes
• Experience integrating Stripe is a plus
""" + PAD)
JOBS["F title only"] = ("Technical Program Manager", "Litware", "")
JOBS["G senior product, integration platform"] = ("Senior Product Manager, Integrations Platform", "Proseware", """About the role
Own the roadmap for our partner integrations platform.

Responsibilities
• Define the product roadmap and prioritize partner integration work
• Work with engineering to deliver APIs and connectors for ERP and CMS partners
• Report product outcomes to executive stakeholders

Requirements
• 8+ years of product management experience
• Experience with ERP or CMS integrations
• Strong cross-functional leadership
""" + PAD)
JOBS["H excluded domain"] = ("Clinical Program Manager", "Wingtip Health", GOOD)
JOBS["I program manager, unrelated domain"] = ("Program Manager, Events", "Humongous Events", """About the role
Plan and run our annual conference series.

Responsibilities
• Own the program calendar, venues, speakers and sponsors
• Coordinate vendors and volunteers on site
• Report attendance and budget to the leadership team

Requirements
• 5+ years of event program management experience
• Strong vendor negotiation skills
• Ability to travel
""" + PAD)
JOBS["J exec stretch"] = ("Vice President, Transformation", "Trey Research", """About the role
Lead enterprise transformation across 40 countries.

Responsibilities
• Own the global transformation roadmap and the P&L impact
• Lead a 200-person organisation and report to the CEO
• Drive ERP consolidation across business units

Requirements
• 18+ years leading global transformation
• Must have led an organisation of 150+ people
• Deep ERP experience
""" + PAD)
JOBS["K paraphrase, no profile vocabulary"] = ("Rollout Captain, Merchant Onboarding", "Initech Commerce", """About the role
Get new merchants live on our platform, smoothly and on a date.

Responsibilities
• Run each merchant launch from signed contract to first live order
• Line up catalog feeds, billing set-up and storefront connections with the integration engineers
• Keep an honest launch tracker and escalate slips to the director
• Hold a weekly call with every merchant champion

Requirements
• 6+ years launching enterprise customers onto a software platform
• Comfortable explaining technical constraints to non-technical owners
""" + PAD)
JOBS["L solutions architect, adjacent craft"] = ("Solutions Architect, Commerce", "Globex Cloud", """About the role
Design reference architectures for our commerce customers.

Responsibilities
• Run discovery with customer architects and write integration designs
• Prototype ERP and payment integrations and review partner implementations
• Advise delivery teams on design trade-offs

Requirements
• 8+ years designing enterprise integrations
• Deep knowledge of ERP and payment platforms
• Hands-on experience with APIs and event-driven design
""" + PAD)

# PROVISIONAL, not approved: case -> (static baseline decision, a stand-in capability answer, the (score, decision) the envelope returns for it)
PROVISIONAL = {
    "A clear keyword match": ("Go", 94, (94, "Go")),
    "B paraphrased delivery lead": ("Go", 92, (92, "Go")),
    "C adjacent implementation role": ("Go", 78, (78, "Go")),
    "D junior title, keywords stuffed": ("No-Go", 85, (55, "No-Go")),             # level cap
    "E hands-on engineering mismatch": ("No-Go", 70, (40, "No-Go")),             # hard-family cap
    "F title only": ("Unscorable", 90, (None, "Unscorable")),                    # never a percentage
    "G senior product, integration platform": ("Go", 82, (82, "Go")),
    "H excluded domain": ("No-Go", 90, (90, "No-Go")),                           # the gate decides, the score stays visible
    "I program manager, unrelated domain": ("No-Go", 20, (20, "No-Go")),
    "J exec stretch": ("Go", 58, (58, "No-Go")),                                 # static overrates a VP stretch
    "K paraphrase, no profile vocabulary": ("No-Go", 78, (78, "Go")),            # static misses a semantic transfer
    "L solutions architect, adjacent craft": ("Go", 70, (70, "No-Go")),          # borderline, adjacent craft
}


def answer(score, rationale="Aligned on delivery ownership.", confidence="medium"):
    return json.dumps({"score": score, "rationale": rationale, "confidence": confidence})


def sent_for(title, text):
    return judge.request(title, text, {"years": 15})


class RepresentativeSet(unittest.TestCase):
    def test_baseline_beside_the_provisional_outcome_for_every_case(self):
        self.assertEqual(set(JOBS), set(PROVISIONAL))
        for name, (title, company, text) in JOBS.items():
            base = evaluate(title, company, text, PROFILE, TODAY)
            decision, raw, want = PROVISIONAL[name]
            got = judge.accept(answer(raw), base, title, sent_for(title, text))
            self.assertEqual(base.decision, decision, name)
            self.assertEqual((got.score, got.decision), want, name)

    def test_the_provisional_outcomes_differ_from_the_static_decision_in_three_cases(self):
        flips = [n for n, (title, company, text) in JOBS.items()
                 if evaluate(title, company, text, PROFILE, TODAY).decision != PROVISIONAL[n][2][1] and PROVISIONAL[n][0] != "Unscorable"]
        self.assertEqual(sorted(flips), ["J exec stretch", "K paraphrase, no profile vocabulary", "L solutions architect, adjacent craft"])


class Envelope(unittest.TestCase):
    title, company, text = JOBS["B paraphrased delivery lead"]
    base = evaluate(title, company, text, PROFILE, TODAY)
    sent = sent_for(title, text)

    def test_the_floor_is_72_and_code_decides(self):
        self.assertEqual(judge.accept(answer(72), self.base, self.title, self.sent).decision, "Go")
        self.assertEqual(judge.accept(answer(71), self.base, self.title, self.sent).decision, "No-Go")

    def test_an_unscorable_posting_never_gets_a_percentage_whatever_the_capability_says(self):
        base = evaluate("Technical Program Manager", "Litware", "", PROFILE, TODAY)
        sent = sent_for("Technical Program Manager", "")
        self.assertIsNone(sent)                                                        # nothing is sent for a title-only posting
        got = judge.accept(answer(99), base, "Technical Program Manager", sent)
        self.assertEqual((got.score, got.decision), (None, "Unscorable"))
        self.assertIsNone(judge.accept("not json at all", base, "x", sent).score)      # the answer is not even read

    def test_unusable_answers_fail_closed(self):
        for bad in ("", "nonsense", "[]", "null", answer(101), answer(-1), answer(72.5), json.dumps({"score": True, "rationale": "x", "confidence": "low"}),
                    json.dumps({"score": "80", "rationale": "x", "confidence": "low"}), answer(80, confidence="certain"), json.dumps({"score": 80, "confidence": "low"}),
                    json.dumps({"score": 80, "rationale": 5, "confidence": "low"}), json.dumps({"score": 80, "rationale": "x", "confidence": "low", "model": "other"}), None):
            self.assertIsNone(judge.accept(bad, self.base, self.title, self.sent), repr(bad))

    def test_the_rationale_is_bounded_and_single_line(self):
        got = judge.accept(answer(80, "line one\n\n  line two " + "x" * 400), self.base, self.title, self.sent)
        self.assertEqual((len(got.rationale), "\n" in got.rationale), (300, False))

    def test_the_request_carries_title_description_and_profile_but_no_benefits_pay_or_place_under_their_own_headings(self):
        text = self.text + "\nBenefits\nSalary range 150,000 to 180,000 USD\nLocation\nAustin, Texas onsite three days\n"
        sent = sent_for(self.title, text)
        self.assertEqual(sorted(sent), ["description", "profile", "title"])
        self.assertIn("steering forum", sent["description"])
        self.assertNotIn("Salary", sent["description"])
        self.assertNotIn("Austin", sent["description"])

    def test_pay_place_and_work_mode_stated_inside_a_retained_section_are_removed_before_egress(self):
        text = self.text.replace("• Run the weekly steering forum and publish decisions",
                                 "• Run the weekly steering forum and publish decisions\n• This is a hybrid role based in Austin, Texas, three days a week in the office\n"
                                 "• Coordinate remote engineering teams across time zones. Salary range $150,000 to $180,000 plus a 401(k) match\n"
                                 "• Track benefits realization for each program; Relocation assistance is available") \
                          .replace("• Clear written and verbal communication with executives", "• Clear written and verbal communication with executives\n• Must be authorized to work in the US; visa sponsorship is not offered")
        description = sent_for(self.title, text)["description"]
        for leaked in ("hybrid", "Austin", "three days", "Salary", "$150,000", "401(k)", "Relocation", "visa", "authorized to work"):
            self.assertNotIn(leaked, description, leaked)
        for kept in ("steering forum", "remote engineering teams", "benefits realization", "10+ years leading complex"):
            self.assertIn(kept, description, kept)                                     # legitimate responsibilities survive

    def test_a_posting_that_is_mostly_policy_statements_sends_nothing(self):
        text = "Responsibilities\n" + "\n".join(f"• Salary range ${n}0,000, hybrid in Austin, 401(k) and health insurance" for n in range(1, 12)) + "\n"
        self.assertGreater(len(text), 300)
        self.assertIsNone(sent_for(self.title, text))

    def test_a_title_carrying_work_mode_or_place_is_cleaned_before_egress(self):
        for raw, kept in (("Technical Program Manager — Remote US", "Technical Program Manager"), ("Product Manager (Hybrid, Chicago)", "Product Manager"),
                          ("Program Manager | Onsite | Austin", "Program Manager"), ("Technical Program Manager, Remote US", "Technical Program Manager"),
                          ("Remote Delivery Lead", "Delivery Lead"), ("Senior Manager, Operations and Implementation", "Senior Manager, Operations and Implementation")):
            self.assertEqual(sent_for(raw, self.text)["title"], kept, raw)

    def test_commuting_and_residency_requirements_without_a_place_keyword_are_removed(self):
        text = self.text.replace("• Run the weekly steering forum and publish decisions", "• Run the weekly steering forum and publish decisions\n• Must commute to Chicago every Monday\n• Candidates must reside near the hub")
        description = sent_for(self.title, text)["description"]
        for leaked in ("commute", "Chicago", "reside"):
            self.assertNotIn(leaked, description, leaked)
        self.assertIn("steering forum", description)

    def test_the_residual_limit_is_explicit_so_zero_leakage_is_never_claimed(self):
        self.assertIn("RESIDUAL LIMIT", judge.__doc__)
        self.assertIn("no model egress may be enabled", judge.__doc__.lower())
        self.assertEqual(sent_for("Product Manager, Chicago", self.text)["title"], "Product Manager, Chicago")      # a place with no cue is NOT recognised: closing this gap needs a new boundary, then this test changes

    BOILERPLATE = ("About the Role\nWe are a mission-driven company reimagining how teams everywhere collaborate. Our culture rewards curiosity, kindness and bold ideas, and our customers "
                   "span every industry and continent. Join a passionate group that celebrates wins together, learns from every setback and believes the best work happens when people feel they belong "
                   "and are trusted to do their best work every single day.\n")

    def test_long_generic_company_text_without_role_evidence_is_not_scorable(self):
        for text in (self.BOILERPLATE, self.BOILERPLATE.replace("About the Role", "About the Company"), self.BOILERPLATE + "\nResponsibilities\n• Support the team\n"):
            self.assertGreater(len(text), 300)
            sent = sent_for("Program Manager", text)
            self.assertIsNone(sent)
            got = judge.accept(answer(90), evaluate("Program Manager", "Initech", text, PROFILE, TODAY), "Program Manager", sent)
            self.assertEqual((got.score, got.decision), (None, "Unscorable"))

    NARRATIVE = ("About the role\nGet merchants live, smoothly and on a date.\n\nWhat you'll do\n• Take each merchant from signed contract to first live order\n"
                 "• Keep a tracker the whole group trusts and flag slips early\n• Hold a weekly call with every merchant champion\n"
                 "• Explain slips, reset expectations and protect the next launch\n• Write plain status notes that close loops before anyone has to ask\n"
                 "• Keep the next three launches warm so nobody waits on a handoff\n")

    def test_a_substantive_paraphrased_description_is_judged_even_when_the_static_scorer_recognises_nothing(self):
        base = evaluate("Launch Captain", "Initech", self.NARRATIVE, PROFILE, TODAY)
        self.assertEqual((base.score, base.decision), (None, "Unscorable"))          # static vocabulary limit: nothing recognised
        sent = sent_for("Launch Captain", self.NARRATIVE)
        self.assertIsNotNone(sent)
        got = judge.accept(answer(79), base, "Launch Captain", sent)
        self.assertEqual((got.score, got.decision), (79, "Go"))

    def test_the_family_cap_and_the_gate_hold_when_the_static_scorer_recognises_nothing(self):
        engineer = evaluate("Software Engineer", "Initech", self.NARRATIVE, PROFILE, TODAY)
        self.assertIsNone(engineer.score)
        got = judge.accept(answer(90), engineer, "Software Engineer", sent_for("Software Engineer", self.NARRATIVE))
        self.assertEqual((got.score, got.decision), (40, "No-Go"))                   # hard-family cap, code-owned
        gated = evaluate("Program Manager", "AcmeCorp", self.NARRATIVE, PROFILE, TODAY)
        got = judge.accept(answer(90), gated, "Program Manager", sent_for("Program Manager", self.NARRATIVE))
        self.assertEqual((got.score, got.decision), (90, "No-Go"))                   # the private exclusion gate decides


if __name__ == "__main__":
    unittest.main()
