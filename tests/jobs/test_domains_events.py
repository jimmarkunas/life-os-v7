"""LIFE-604 item 11: which domains.yml jobs run for which trigger. The real job-level `if:` expressions are evaluated (the evaluator of tests/jobs/fit/test_backlog_workflow.py) for
every kind of trigger, and the set of jobs that run is compared with the recorded table (tests/jobs/domains_event_table.json). The table was taken from the workflow BEFORE the
trigger clause was moved into one gate job, so it passing after the change is the proof that run or skip is identical for every event. To change what a trigger runs on purpose,
regenerate the file with `python -m tests.jobs.test_domains_events --write` and say why in the PR."""
import json
import pathlib
import sys
import unittest

try:
    import yaml
except ImportError:
    yaml = None

from tests.jobs.fit.test_backlog_workflow import evaluate, truthy

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "domains.yml"
TABLE = pathlib.Path(__file__).resolve().parent / "domains_event_table.json"
GATES = ("gate",)                                       # the one gate job; compared separately (see test_the_gate_runs_exactly_...)
TRIGGER_INPUTS = ("all", "amazon", "bills_paid", "calendar_sync", "dcc", "hiring", "imessage", "jira", "live", "megibow", "physical_mail", "recruiters", "refresh")


def load():
    return yaml.safe_load(WORKFLOW.read_text())


def order(jobs):
    """Jobs in dependency order (a job after everything it needs)."""
    done, out = set(), []

    def visit(name):
        if name in done:
            return
        done.add(name)
        needs = jobs[name].get("needs") or []
        for n in ([needs] if isinstance(needs, str) else needs):
            visit(n)
        out.append(name)
    for name in jobs:
        visit(name)
    return out


def events(doc):
    """-> {label: (event_name, workflow_run fields, inputs, vars, jobs that run and fail)}: every kind of trigger the workflow can get."""
    spec = doc[True]["workflow_dispatch"]["inputs"]
    base = {k: v.get("default", "" if v.get("type") == "string" else False) for k, v in spec.items()}
    out = {}
    for conclusion in ("success", "failure", "cancelled", "skipped"):
        for title in ("tick", "manual", "quick"):
            out[f"after hourly: {title}, {conclusion}"] = ("workflow_run", {"conclusion": conclusion, "display_title": title}, {}, {}, ())
    for variables in ({"V7_IMESSAGE_ALERTS": "true"}, {"GRANOLA_AUTO": "off"}, {"V7_IMESSAGE_ALERTS": "true", "GRANOLA_AUTO": "off"}):
        out["after hourly: tick, success, " + ", ".join(f"{k}={v}" for k, v in variables.items())] = ("workflow_run", {"conclusion": "success", "display_title": "tick"}, {}, variables, ())
    for broken in ("jira", "amazon", "recruiters"):                              # the report job runs only when some job failed
        out[f"after hourly: tick, success, {broken} fails"] = ("workflow_run", {"conclusion": "success", "display_title": "tick"}, {}, {}, (broken,))
        out[f"dispatch: all=True, {broken} fails"] = ("workflow_dispatch", {}, dict(base, all=True), {}, (broken,))
    out["dispatch: no input"] = ("workflow_dispatch", {}, dict(base), {}, ())
    for name in TRIGGER_INPUTS:
        if name not in spec:
            continue
        kind = spec[name].get("type")
        values = spec[name].get("options") if kind == "choice" else ([True] if kind == "boolean" else ["x"])
        for value in values:
            given = dict(base)
            given[name] = value
            out[f"dispatch: {name}={value}"] = ("workflow_dispatch", {}, given, {}, ())
            if name != "live" and value not in (False, "none", ""):
                withlive = dict(given, live=True)
                out[f"dispatch: {name}={value} + live"] = ("workflow_dispatch", {}, withlive, {}, ())
    return out


def runs(doc, event, not_due=()):
    """-> sorted names of the jobs that run for one trigger (every job that runs is taken to succeed)."""
    name, run_fields, inputs, variables, failing = event
    due = {f"due_{job}": ("false" if job in not_due and name == "workflow_run" else "true") for job in doc["jobs"]}        # the gate's due step: every job due unless the test says otherwise (a tick only)
    ctx = {"github": {"event_name": name, "event": {"workflow_run": run_fields}}, "inputs": inputs, "vars": variables, "env": {}, "steps": {"due": {"outputs": due}}}
    results = {}
    for job in order(doc["jobs"]):
        spec = doc["jobs"][job]
        needs = spec.get("needs") or []
        needs = [needs] if isinstance(needs, str) else needs
        ctx["needs"] = {n: {"result": results.get(n, "skipped"), "outputs": results.get(n + ".outputs", {})} for n in needs}
        condition = spec.get("if")
        ok = truthy(evaluate(str(condition), ctx)) if condition is not None else all(results.get(n) == "success" for n in needs)
        results[job] = ("failure" if job in failing else "success") if ok else "skipped"
        if ok and spec.get("outputs"):
            results[job + ".outputs"] = {k: str(evaluate(str(v), ctx)).lower() for k, v in spec["outputs"].items()}
    return sorted(j for j, r in results.items() if r in ("success", "failure") and "." not in j)


def domain_jobs(names):
    return [n for n in names if n not in GATES]


def gate_expected(event):
    """The gate's trigger from the event alone, written out independently of the workflow text: a completed tick (not cancelled) or `all`."""
    name, run_fields, inputs, variables, failing = event
    tick = name == "workflow_run" and run_fields.get("conclusion") != "cancelled" and run_fields.get("display_title") == "tick"
    return bool(tick or inputs.get("all"))


def table(doc=None):
    doc = doc or load()
    return {label: runs(doc, ev) for label, ev in sorted(events(doc).items())}


@unittest.skipIf(yaml is None, "PyYAML unavailable")
class DomainsEventTable(unittest.TestCase):
    def test_every_trigger_runs_exactly_the_recorded_jobs(self):
        recorded = json.loads(TABLE.read_text())
        current = table()
        self.assertEqual(sorted(current), sorted(recorded), "the set of triggers changed")
        for label in sorted(recorded):
            self.assertEqual(domain_jobs(current[label]), domain_jobs(recorded[label]), label)

    def test_a_clean_tick_runs_the_chain_a_failure_adds_the_report_and_a_cancelled_or_manual_run_runs_nothing(self):
        current = table()
        chain = {"jira", "agenda", "bills", "amazon", "amazon_retention", "mail", "hiring", "dcc", "megibow", "recruiters", "outlook", "granola"}
        self.assertEqual(chain, set(domain_jobs(current["after hourly: tick, success"])))
        self.assertEqual(set(domain_jobs(current["after hourly: tick, success, jira fails"])), chain | {"report"})        # the report job exists to name a failed job
        for label in ("after hourly: tick, cancelled", "after hourly: manual, success", "after hourly: quick, success", "dispatch: no input"):
            self.assertEqual(domain_jobs(current[label]), [], label)

    def test_the_gate_runs_exactly_for_a_tick_or_all(self):
        doc = load()
        self.assertIn("gate", doc["jobs"])
        for label, event in sorted(events(doc).items()):
            self.assertEqual("gate" in runs(doc, event), gate_expected(event), label)

    def test_every_domain_job_waits_for_the_gate_and_the_trigger_clause_exists_once(self):
        doc = load()
        text = WORKFLOW.read_text()
        for name, job in doc["jobs"].items():
            if name != "gate":
                self.assertIn("gate", job["needs"], name)
        self.assertEqual(text.count("github.event.workflow_run.display_title == 'tick'"), 2, "the trigger clause is stated once in the gate job (its condition and its tick output)")

    def test_a_job_that_is_not_due_is_skipped_on_a_tick_and_never_on_its_own_input_or_on_all(self):
        """LIFE-609: the due output decides a tick; a manual run of that one job, and `all` are not held back by the table."""
        doc = load()
        events_ = events(doc)
        tick = events_["after hourly: tick, success"]
        self.assertNotIn("mail", runs(doc, tick, not_due=("mail",)))
        self.assertIn("mail", runs(doc, tick))
        self.assertIn("mail", runs(doc, events_["dispatch: physical_mail=True"], not_due=("mail",)))
        self.assertIn("mail", runs(doc, events_["dispatch: all=True"], not_due=("mail",)))
        others = [j for j in runs(doc, tick) if j != "mail"]
        self.assertEqual(sorted(runs(doc, tick, not_due=("mail",))), sorted(others))               # nothing else changes with it

    def test_a_skipped_job_does_not_stop_the_jobs_that_come_after_it_or_trigger_the_failure_push(self):
        doc = load()
        tick = events(doc)["after hourly: tick, success, V7_IMESSAGE_ALERTS=true"]
        ran = runs(doc, tick, not_due=("mail", "amazon", "megibow"))
        for job in ("hiring", "dcc", "imessage", "amazon_retention", "jira"):
            self.assertIn(job, ran, job)                                                            # they all use always() or !cancelled(), so a skipped need does not stop them
        self.assertNotIn("report", ran)                                                             # a skipped job is not a failure

    def test_the_negative_control_a_changed_trigger_is_caught(self):
        doc = load()
        doc["jobs"]["bills"]["if"] = "${{ always() }}"
        recorded = json.loads(TABLE.read_text())
        current = table(doc)
        self.assertTrue(any(domain_jobs(current[k]) != domain_jobs(recorded[k]) for k in recorded))

    def test_the_negative_control_a_gate_that_always_runs_is_caught(self):
        doc = load()
        doc["jobs"]["gate"]["if"] = "${{ always() }}"
        self.assertTrue(any(("gate" in runs(doc, ev)) != gate_expected(ev) for ev in events(doc).values()))


if __name__ == "__main__":
    if "--write" in sys.argv:
        TABLE.write_text(json.dumps(table(), indent=1, sort_keys=True) + "\n")
        print("wrote", TABLE)
    else:
        unittest.main()
