"""D219 (LIFE-41 10586): a Cloud Fit backlog dispatch runs alone. The real hourly.yml `if:` expressions are evaluated (a small exact evaluator for the forms the
file uses: literals, property paths, ! == != && || ( ), always(), startsWith()) for each trigger shape, so the job/step plan is proven without running Actions:
a cloud_backlog-only live dispatch never reaches the newsletter sweep, extract, a resolver job, discovery, web, publish or any TinyFish/Gmail stage; cloud_backlog
with any other action input fails at prep's first step before any credential or stage; ticks and every other manual run keep their plan."""
import pathlib
import re
import unittest

try:
    import yaml
except ImportError:                                                         # the workflow contract tests skip without PyYAML, as the others do
    yaml = None

WORKFLOW = pathlib.Path(__file__).resolve().parents[3] / ".github" / "workflows" / "hourly.yml"
TOKEN = re.compile(r"\s*(?:(\$\{\{)|(\}\})|('(?:[^']|'')*')|(&&|\|\||==|!=|!|\(|\)|,)|(-?\d+(?:\.\d+)?)|([A-Za-z_][\w\-]*(?:\.[\w\-]+)*))")


def truthy(v):
    return not (v is None or v is False or v == "" or v == 0)


def evaluate(text, ctx):
    toks, i = [], 0
    while i < len(text):
        m = TOKEN.match(text, i)
        if not m or m.end() == i:
            assert text[i:].strip() == "", text[i:i + 30]
            break
        i = m.end()
        if not (m.group(1) or m.group(2)):
            toks.append(m.group(0).strip())
    pos = [0]
    peek = lambda: toks[pos[0]] if pos[0] < len(toks) else None                          # noqa: E731

    def take():
        pos[0] += 1
        return toks[pos[0] - 1]

    def primary():
        t = take()
        if t == "(":
            v = orexpr()
            assert take() == ")"
            return v
        if t == "!":
            return not truthy(primary())
        if t.startswith("'"):
            return t[1:-1].replace("''", "'")
        if t in ("true", "false"):
            return t == "true"
        if re.fullmatch(r"-?\d+(\.\d+)?", t):
            return float(t)
        if peek() == "(":
            take()
            args = []
            while peek() != ")":
                args.append(orexpr())
                if peek() == ",":
                    take()
            take()
            assert t in ("always", "startsWith"), t
            return True if t == "always" else isinstance(args[0], str) and args[0].lower().startswith(str(args[1]).lower())
        cur = ctx
        for part in t.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        return cur

    def cmp():
        left = primary()
        while peek() in ("==", "!="):
            op, right = take(), primary()
            same = (left.lower() if isinstance(left, str) else left) == (right.lower() if isinstance(right, str) else right)
            left = same if op == "==" else not same
        return left

    def andexpr():
        left = cmp()
        while peek() == "&&":
            take()
            right = cmp()
            left = right if truthy(left) else left
        return left

    def orexpr():
        left = andexpr()
        while peek() == "||":
            take()
            right = andexpr()
            left = left if truthy(left) else right
        return left

    value = orexpr()
    assert pos[0] == len(toks), toks[pos[0]:]
    return value


def context(doc, event, variables=None, **given):
    inputs = {k: v.get("default", "" if v.get("type") == "string" else False) for k, v in doc[True]["workflow_dispatch"]["inputs"].items()} if event == "workflow_dispatch" else {}
    inputs.update(given)
    ctx = {"github": {"event_name": event}, "inputs": inputs, "vars": {"V7_PIPELINE": "true", "V7_CLOUD_FIT": "off", "V7_FIT_GATE": "true", **(variables or {})}, "steps": {}}
    ctx["env"] = {"PIPELINE": evaluate(doc["env"]["PIPELINE"], ctx)}
    return ctx


def plan(doc, event, variables=None, **given):
    """-> {job: (result, [executed steps])} for jobs that run. A step runs only while nothing before it failed unless its `if` calls always()/failure()/cancelled()
    (GitHub's implicit success() &&); the combined-input guard fails its job."""
    ctx = context(doc, event, variables, **given)
    out, results = {}, {}
    for name, job in doc["jobs"].items():
        needs = job.get("needs") or []
        needs = [needs] if isinstance(needs, str) else needs
        ctx["needs"] = {n: {"result": results.get(n, "skipped"), "outputs": {}} for n in needs}
        runs = truthy(evaluate(str(job["if"]), ctx)) if job.get("if") else all(results.get(n) == "success" for n in needs)
        if not runs:
            results[name] = "skipped"
            continue
        steps, failed = [], False
        for step in job.get("steps", []):
            cond = str(step.get("if") or "")
            status_aware = any(f in cond for f in ("always(", "failure(", "cancelled("))       # otherwise GitHub prefixes an implicit success() &&
            if (not failed or status_aware) and (truthy(evaluate(cond, ctx)) if cond else True):
                steps.append(step)
                failed = failed or "CLOUD_BACKLOG_COMBINED_INPUTS" in str(step.get("run", ""))
        results[name] = "failure" if failed else "success"
        out[name] = (results[name], steps)
    return out


LOADERS = ("bitwarden/sm-action",)
ALLOWED_RUNS = ("python3 -m lifeos.platform.gate", "pip install pyyaml", "python -m unittest discover", "python -m lifeos.run fit-cloud-backlog", "failed=\"\"", "alerts push")


@unittest.skipIf(yaml is None, "PyYAML unavailable")
class CloudBacklogRunsAlone(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load(WORKFLOW.read_text())
        self.inputs = self.doc[True]["workflow_dispatch"]["inputs"]

    def test_a_cloud_backlog_only_live_dispatch_runs_only_the_batch_and_never_a_source_newsletter_gmail_or_tinyfish_stage(self):
        p = plan(self.doc, "workflow_dispatch", cloud_backlog="1", live=True)
        self.assertEqual(sorted(p), ["finish", "prep", "report"])                                    # jobright / linkedin / lensa / interview never start
        self.assertTrue(all(result == "success" for result, _ in p.values()))
        ran = [s for _, steps in p.values() for s in steps]
        names = [s.get("id") or s.get("name") or s.get("uses") for s in ran]
        self.assertIn("fit_cloud_backlog", names)
        for absent in ("Stage 1 - sweep", "extract", "tick", "fit", "audit", "publish", "web", "discover", "enrich", "boards", "titlewatch", "sponsors", "finalize"):
            self.assertNotIn(absent, names)
        for step in ran:
            run = str(step.get("run") or "")
            if run:
                self.assertTrue(any(a in run for a in ALLOWED_RUNS), (step.get("name"), run[:80]))       # every command that runs is one of these
            env = step.get("env") or {}
            if "TINYFISH_API_KEY" in env or any(k.startswith("GMAIL_") for k in env):
                self.assertIn("python3 -m lifeos.platform.gate", run, step.get("name"))                 # only the gate step is handed them, and it never reads them
        gate = (WORKFLOW.parents[2] / "lifeos" / "platform" / "gate.py").read_text()
        self.assertNotIn("TINYFISH", gate)
        self.assertNotRegex(gate, r"GMAIL_OAUTH")

    def test_cloud_backlog_with_any_other_action_input_fails_first_before_any_credential_or_stage(self):
        actions = [k for k in self.inputs if k not in ("live", "cloud_backlog", "publish_limit")]     # every other action, read from the workflow: a new input is covered
        self.assertGreater(len(actions), 20)
        for name in actions:
            value = "x" if self.inputs[name].get("type") == "string" else True
            p = plan(self.doc, "workflow_dispatch", cloud_backlog="3", live=True, **{name: value})
            self.assertEqual(list(p), ["prep"], name)
            result, steps = p["prep"]
            self.assertEqual(result, "failure", name)
            self.assertEqual([st.get("uses") for st in steps[:1]], ["actions/checkout@v5"], name)          # only the repository checkout ran before it: no credential
            self.assertEqual(len(steps), 2, name)
            self.assertIn("CLOUD_BACKLOG_COMBINED_INPUTS", steps[1]["run"])
            self.assertIn("exit 1", steps[1]["run"])
        self.assertEqual(self.doc["jobs"]["prep"]["steps"][1]["name"].split(" (")[0], "Cloud Fit backlog runs alone")

    def test_ticks_and_every_other_manual_run_keep_their_plan(self):
        names = lambda p, job: [s.get("id") or s.get("name") for s in p[job][1]]                         # noqa: E731
        for shape in ({"event": "schedule"}, {"event": "workflow_dispatch", "tick": True, "live": True}):
            event = shape.pop("event")
            p = plan(self.doc, event, **shape)
            self.assertIn("Stage 1 - sweep", names(p, "prep"))
            self.assertIn("extract", names(p, "prep"))
            self.assertIn("tick", names(p, "finish"))
            self.assertNotIn("fit_cloud_backlog", names(p, "finish"))
        for given in ({"fit": True, "publish": True, "live": True}, {"discover": True}, {"quick": True, "live": True}, {"cloud_backlog": "0", "fit": True}, {"cloud_backlog": "", "web": True}):
            p = plan(self.doc, "workflow_dispatch", **given)
            self.assertIn("Stage 1 - sweep", names(p, "prep"), given)
            self.assertNotIn("Cloud Fit backlog runs alone", " ".join(str(n) for n in names(p, "prep")), given)
            self.assertNotIn("fit_cloud_backlog", names(p, "finish"), given)
            if not given.get("quick"):
                self.assertTrue({"jobright", "linkedin", "lensa"} <= set(p), given)                    # a non-backlog manual run still starts the resolver jobs

    def test_a_failed_batch_still_fails_the_run(self):
        p = plan(self.doc, "workflow_dispatch", cloud_backlog="1", live=True)
        lanes = next(s for s in p["finish"][1] if s.get("id") == "lanes")
        self.assertIn("fit_cloud_backlog=${{ steps.fit_cloud_backlog.outcome }}", lanes["run"])


@unittest.skipIf(yaml is None, "PyYAML unavailable")
class ScheduledBatch(unittest.TestCase):
    """LIFE-41 (Jim, Oct 19 2026): "the canary is too little - we need the jobs coming in on schedule". V7_CLOUD_FIT_SCHEDULED=N runs the same batch after every
    tick, N calls, with N as its own ceiling; unset or 0 keeps ticks exactly as they were."""

    def setUp(self):
        self.doc = yaml.safe_load(WORKFLOW.read_text())
        self.step = next(s for s in self.doc["jobs"]["finish"]["steps"] if s.get("id") == "fit_cloud_backlog")

    def names(self, p):
        return [s.get("id") or s.get("name") for s in p["finish"][1]]

    def env(self, event, variables, **given):
        ctx = context(self.doc, event, variables, **given)
        return {k: evaluate(v, ctx) for k, v in self.step["env"].items() if k.startswith(("CLOUD_BACKLOG", "V7_CLOUD_FIT_BACKLOG"))}

    def test_a_tick_with_the_variable_runs_the_batch_after_the_tick_with_its_own_ceiling_budget_and_no_page(self):
        on = {"V7_CLOUD_FIT_SCHEDULED": "20", "V7_CLOUD_FIT_BACKLOG_PAGE": "0" * 32, "V7_CLOUD_FIT_BACKLOG_MAX": "1"}
        for event, given in (("schedule", {}), ("workflow_dispatch", {"tick": True, "live": True})):
            names = self.names(plan(self.doc, event, on, **given))
            self.assertIn("fit_cloud_backlog", names, event)
            self.assertLess(names.index("tick"), names.index("fit_cloud_backlog"), event)                 # after the tick: this hour's new jobs are candidates
            self.assertTrue(any("join the tailnet" in str(n) for n in names), event)                     # the only route to Oracle
            self.assertEqual(self.env(event, on, **given), {"CLOUD_BACKLOG": "20", "V7_CLOUD_FIT_BACKLOG_MAX": "20", "V7_CLOUD_FIT_BACKLOG_BUDGET": "600",
                                                            "V7_CLOUD_FIT_BACKLOG_PAGE": ""}, event)      # a leftover pilot page never narrows a tick

    def test_without_the_variable_a_tick_is_unchanged(self):
        for variables in ({}, {"V7_CLOUD_FIT_SCHEDULED": "0"}, {"V7_CLOUD_FIT_SCHEDULED": ""}):
            for event, given in (("schedule", {}), ("workflow_dispatch", {"tick": True, "live": True})):
                names = self.names(plan(self.doc, event, variables, **given))
                self.assertIn("tick", names)
                self.assertNotIn("fit_cloud_backlog", names, (variables, event))
                self.assertTrue(any("join the tailnet" in str(n) for n in names), (variables, event))      # LIFE-85: the finish job always joins (the store's Oracle fallback)

    def test_a_parked_pipeline_never_runs_it(self):
        p = plan(self.doc, "schedule", {"V7_PIPELINE": "false", "V7_CLOUD_FIT_SCHEDULED": "20"})
        self.assertNotIn("finish", p)

    def test_a_manual_batch_keeps_its_input_ceiling_budget_and_page(self):
        variables = {"V7_CLOUD_FIT_SCHEDULED": "20", "V7_CLOUD_FIT_BACKLOG_MAX": "1", "V7_CLOUD_FIT_BACKLOG_BUDGET": "240", "V7_CLOUD_FIT_BACKLOG_PAGE": "a" * 32}
        self.assertEqual(self.env("workflow_dispatch", variables, cloud_backlog="1", live=True),
                         {"CLOUD_BACKLOG": "1", "V7_CLOUD_FIT_BACKLOG_MAX": "1", "V7_CLOUD_FIT_BACKLOG_BUDGET": "240", "V7_CLOUD_FIT_BACKLOG_PAGE": "a" * 32})
        self.assertNotIn("tick", self.names(plan(self.doc, "workflow_dispatch", variables, cloud_backlog="1", live=True)))


if __name__ == "__main__":
    unittest.main()
