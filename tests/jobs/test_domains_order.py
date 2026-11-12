"""LIFE-586: a domains job that COMPARES other cards' regions before and after its own write must not overlap a job that writes one of those regions, or the other card's legitimate write
looks like a violation and fails it. This reads the real `needs:` graph of domains.yml and checks that every such pair is ordered (one transitively waits for the other). The cards that were
converted to the write-scope guard (amazon, mail, attention, dcc) compare nothing, so they are free to start early; the cards that still compare keep waiting for what they compare."""
import pathlib
import unittest

try:
    import yaml
except ImportError:
    yaml = None

from lifeos.imessage import comms_card
from lifeos.platform import router

WORKFLOW = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "domains.yml"
WRITER = {router.JIRA_REGION: "jira", router.CALENDAR_REGION: "agenda", router.BILLS_REGION: "bills", router.AMAZON_REGION: "amazon", router.MAIL_ALERTS_REGION: "mail",
          router.ATTENTION_REGION: "attention", router.HIRING_REGION: "hiring", router.DCC_REGION: "dcc"}          # the job that writes each region
# job -> the jobs whose regions it compares before and after its own write. Taken from the cards' own lists where they have one; hiring digests the whole page outside its own blocks.
COMPARES = {
    "bills": {"jira", "agenda"},                                                    # lifeos/bills/card.py _protected: the Jira and Calendar regions
    "agenda": {"jira"},                                                             # lifeos/agenda/card.py: the Jira region
    "imessage": {WRITER[region] for _, region in comms_card.PROTECTED},             # lifeos/imessage/comms_card.py PROTECTED
    "hiring": {"jira", "agenda", "bills", "amazon", "mail", "attention", "imessage"},       # lifeos/hiring/card.py: the whole page outside the Hiring region
}


def needs_of(jobs):
    out = {}
    for name, job in jobs.items():
        n = job.get("needs") or []
        out[name] = {n} if isinstance(n, str) else set(n)
    return out


def waits_for(graph, job, other):
    """True when `job` transitively needs `other`."""
    seen, todo = set(), [job]
    while todo:
        for need in graph.get(todo.pop(), ()):
            if need == other:
                return True
            if need not in seen:
                seen.add(need)
                todo.append(need)
    return False


def unordered_pairs(graph):
    return sorted((c, w) for c, writers in COMPARES.items() for w in writers if not waits_for(graph, c, w) and not waits_for(graph, w, c))


@unittest.skipIf(yaml is None, "PyYAML unavailable")
class ComparingCardsAreOrdered(unittest.TestCase):
    def setUp(self):
        self.jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
        self.graph = needs_of(self.jobs)

    def test_every_card_that_compares_regions_is_ordered_against_the_cards_it_compares(self):
        self.assertEqual(unordered_pairs(self.graph), [])

    def test_the_converted_cards_start_without_waiting_for_the_early_cards(self):
        for job in ("amazon", "mail"):
            self.assertEqual(self.graph[job], {"gate"}, job)

    def test_dropping_a_wait_that_a_comparing_card_needs_is_caught(self):
        for comparer, dropped in (("imessage", "amazon"), ("hiring", "mail"), ("hiring", "imessage"), ("bills", "agenda")):
            graph = {k: set(v) for k, v in self.graph.items()}
            graph[comparer] = graph[comparer] - {dropped}
            for other, needs in graph.items():                                        # remove the indirect route too, so only the direct wait mattered
                if other != comparer and dropped in needs and comparer in needs:
                    needs.discard(dropped)
            self.assertIn((comparer, dropped), unordered_pairs(graph), (comparer, dropped))


if __name__ == "__main__":
    unittest.main()
