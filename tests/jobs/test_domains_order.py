"""LIFE-586 / LIFE-613: a domains job that COMPARES other cards' regions before and after its own write must not overlap a job that writes one of those regions, or the other card's legitimate write
looks like a violation and fails it. This reads the real `needs:` graph of domains.yml and checks that every such pair is ordered (one transitively waits for the other). Every Daily Report card
now writes through the write-scope guard (report_region.ScopedClient), which refuses a block that is not its own BEFORE the write is sent, so no card compares anything and none waits for another;
only DCC, which builds from the other cards' callouts, still runs after them."""
import pathlib
import unittest

try:
    import yaml
except ImportError:
    yaml = None

from lifeos.platform import router

WORKFLOW = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "domains.yml"
WRITER = {router.JIRA_REGION: "jira", router.CALENDAR_REGION: "agenda", router.BILLS_REGION: "bills", router.AMAZON_REGION: "amazon", router.MAIL_ALERTS_REGION: "mail",
          router.ATTENTION_REGION: "attention", router.HIRING_REGION: "hiring", router.DCC_REGION: "dcc"}          # the job that writes each region
# job -> the jobs whose regions it compares before and after its own write. Empty since LIFE-613: bills, agenda, imessage (Communications and Oracle Cloud) and hiring joined amazon, mail, attention
# and dcc on the scope guard. A card that is added with a comparison puts its entry here and must then wait for what it compares.
COMPARES = {}


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

    def test_the_converted_cards_start_without_waiting_for_each_other(self):
        for job in ("amazon", "mail", "agenda", "bills", "imessage", "hiring"):
            self.assertEqual(self.graph[job], {"gate"}, job)

    def test_dcc_still_runs_after_every_card_it_builds_from(self):
        for job in ("jira", "agenda", "bills", "amazon", "attention", "mail", "hiring", "imessage"):
            self.assertTrue(waits_for(self.graph, "dcc", job), job)

    def test_a_comparing_card_that_does_not_wait_for_what_it_compares_is_caught(self):
        graph = {k: set(v) for k, v in self.graph.items()}
        graph["hiring"] = {"gate"}
        found = sorted((c, w) for c, writers in {"hiring": {"mail", "imessage"}}.items() for w in writers if not waits_for(graph, c, w) and not waits_for(graph, w, c))
        self.assertEqual(found, [("hiring", "imessage"), ("hiring", "mail")])

if __name__ == "__main__":
    unittest.main()
