"""the pilot end to end against the simulator (no network, no paid call): the free proofs come first and each one can
stop the run before the guard arms.
"""

import importlib.util
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sim = load("test_um_budget_guard", ROOT / "tests" / "contracts" / "test_um_budget_guard.py")
pilot = load("um_honcho_pilot", ROOT / "tools" / "um_honcho_pilot.py")
um = pilot.um
DOC = json.loads(um.CORPUS.read_text())


class Honcho(sim.Honcho):
    scoped = True
    unarmed_makes_work = False
    oversize_refused = True

    def create_workspace(self):
        return {}

    def create_peers_and_session(self):
        return {}

    def probe_other_workspace(self, name="personal"):
        return self.scoped

    def post_messages(self, batch, armed):
        super().post_messages(batch, armed)
        if self.unarmed_makes_work and not armed:
            self.w.queue.append({"kind": "unit", "costs": [0.001]})

    def probe_oversize_message(self):
        return self.oversize_refused

    def create_conclusions(self, rows):
        self.created = len(rows)
        self.w.posted += len(rows)

    def conclusion_ids(self):
        return ["c1", "c2"]

    def delete_conclusion(self, cid):
        self.deleted = cid

    def dreams(self):
        return [{"id": "r1", "status": "success"}] if self.w.posted else []

    def dream_detail(self, run_id):
        return {"status": "success", "created": [1, 2, 3], "removed": [1], "peer_card_updated": True}

    def recall(self, query, tenant, top_k):
        world = um.World(DOC)
        ids, _ = world.search(tenant, query, top_k)
        return [{"source_ref": i, "text": world.src[i]["text"]} for i in ids]


class Db:
    def __init__(self, before=None, after=None, due=None, due_after=None):
        self.states = [before or {"count": 31, "newest": "2026-11-26 07:42:26", "hash": "h31"}]
        self.after = after
        self.due, self.due_after, self.n = (
            due if due is not None else [],
            due_after if due_after is not None else ["um-t2-fixture-v1"],
            0,
        )

    def personal_state(self):
        self.n += 1
        return self.states[0] if self.n == 1 or self.after is None else self.after

    def due_workspaces(self):
        return self.due if self.n <= 1 else self.due_after

    def residue(self, ws, cid):
        return {"documents": 0, "dream_changes": 0, "peer_card_changes": 1}


class Graph:
    def __init__(self):
        self.calls = 0

    def answer(self, tenant, text):
        self.calls += 1
        ids, lookups = um.answer_direct(um.World(DOC), tenant, text)
        return ids, 1 + lookups

    def deps(self, case):
        self.calls += 1
        return um.deps_graph(um.World(DOC), case)


def go(world=None, db=None, honcho=None, graph=None, seed="messages"):
    w = world or sim.World("typical")
    h = honcho or Honcho(w)
    return w, run(w, h, db or Db(), graph or Graph(), seed)


def run(w, h, db, graph, seed="messages"):
    return pilot.run_pilot(h, sim.Meter(w), db, DOC, graph, sim.Clock(w), reps=2, seed=seed, scope_id="t2")


class Free(unittest.TestCase):
    def test_a_clean_run_goes_through_every_step_in_order_and_ends_bounded(self):
        w, ev = go()
        names = [s["step"] for s in ev["steps"]]
        self.assertEqual(
            names,
            [
                "personal_baseline",
                "due_before",
                "created_all_off",
                "token_refused_on_personal",
                "brake_test_unarmed_message_creates_no_work",
                "oversize_message_refused_by_the_api",
                "guard_run",
            ],
        )
        self.assertNotIn("stopped", ev)
        self.assertLessEqual(w.ours(), 2.0)  # the approved scope
        self.assertEqual(w.armed_posts, 20)

    def test_the_personal_baseline_is_read_fresh_not_assumed_to_be_thirty(self):
        w, ev = go(db=Db(before={"count": 31, "newest": "x", "hash": "a"}))
        self.assertEqual(ev["steps"][0]["count"], 31)
        self.assertTrue(ev["personal_unchanged"])
        w, ev = go(
            db=Db(before={"count": 31, "newest": "x", "hash": "a"}, after={"count": 31, "newest": "x", "hash": "b"})
        )
        self.assertFalse(ev["personal_unchanged"])
        w, ev = go(
            db=Db(before={"count": 30, "newest": "x", "hash": "a"}, after={"count": 31, "newest": "y", "hash": "a"})
        )
        self.assertFalse(ev["personal_unchanged"])

    def test_a_token_that_is_not_scoped_stops_before_anything_is_armed(self):
        w = sim.World("typical")
        h = Honcho(w)
        h.scoped = False
        w, ev = go(w, honcho=h)
        self.assertEqual((ev["stopped"], w.armed_posts), ("token_not_scoped", 0))
        self.assertEqual(w.spent, sim.BASE)

    def test_a_missing_or_wrong_scope_stops_the_pilot_before_anything_is_armed(self):
        for change, reason in (
            ({"scope_gone": True}, "scope_missing"),
            ({"scope_id": "other"}, "scope_mismatch"),
            ({"scope_state": "breached"}, "scope_not_open"),
        ):
            with self.subTest(reason):
                w = sim.World("typical")
                for key, value in change.items():
                    setattr(w, key, value)
                w, ev = go(w)
                self.assertEqual((ev["stopped"], w.armed_posts, w.spent), ("guard_" + reason, 0, sim.BASE))
                self.assertEqual(w.flags, [False, False])

    def test_the_four_modes_the_fresh_baseline_and_the_evidence_are_unchanged_by_the_scope(self):
        w, ev = go()
        self.assertEqual(ev["steps"][0]["step"], "personal_baseline")
        self.assertEqual(sorted(ev["comparison"]), ["combined", "direct", "graph", "honcho"])
        guard_run = ev["steps"][-1]
        self.assertEqual(
            (guard_run["step"], guard_run["scope"]["cap_usd"], guard_run["scope"]["state"]), ("guard_run", 2.0, "open")
        )
        self.assertEqual(guard_run["cleanup"], {"flags": [False, False], "queue_idle": True})

    def test_the_brake_test_stops_the_run_if_an_unarmed_message_creates_work(self):
        w = sim.World("typical")
        h = Honcho(w)
        h.unarmed_makes_work = True
        w, ev = go(w, honcho=h)
        self.assertEqual((ev["stopped"], w.armed_posts), ("unarmed_message_created_work", 0))

    def test_a_due_dream_before_the_run_stops_it_first(self):
        w, ev = go(db=Db(due=["personal"]))
        self.assertEqual((ev["stopped"], len(ev["steps"])), ("due_list_not_empty", 2))
        self.assertEqual(w.posted, 0)

    def test_workspace_flags_that_cannot_be_verified_off_stop_the_run(self):
        w = sim.World("typical")
        h = Honcho(w)
        h.get_flags = lambda: (True, False)
        w, ev = go(w, honcho=h)
        self.assertEqual(ev["stopped"], "flags_unverified")

    def test_an_oversize_message_that_the_api_accepts_stops_the_run_before_the_guard(self):
        w = sim.World("typical")
        h = Honcho(w)
        h.oversize_refused = False
        w, ev = go(w, honcho=h)
        self.assertEqual((ev["stopped"], w.armed_posts), ("oversize_message_accepted", 0))

    def test_seeding_the_conclusions_directly_arms_only_the_dream_and_stays_inside_its_exposure(self):
        w = sim.World("typical", explicit=40)
        h = Honcho(w)
        w, ev = go(w, honcho=h, seed="conclusions")
        self.assertEqual((h.created, w.armed_posts), (20, 0))
        self.assertIn("conclusions_created_directly", [s["step"] for s in ev["steps"]])
        self.assertLessEqual(w.ours(), pilot.budget.dream_worst() + 1e-9)
        self.assertNotIn("stopped", ev)

    def test_the_guard_stopping_is_reported_and_the_comparison_still_runs_on_what_exists(self):
        w, ev = go(world=sim.World("worst"))
        self.assertTrue(ev["stopped"].startswith("guard_"))
        self.assertLessEqual(w.ours(), 2.0)
        self.assertIn("comparison", ev)


class Evidence(unittest.TestCase):
    def test_the_comparison_has_the_four_modes_and_the_aura_arm_is_used(self):
        graph = Graph()
        w, ev = go(graph=graph)
        self.assertEqual(set(ev["comparison"]), {"direct", "graph", "honcho", "combined"})
        self.assertGreater(graph.calls, 0)
        self.assertEqual(ev["comparison"]["honcho"]["metrics"]["fabricated_answers"], 0)

    def test_aggregates_for_the_dream_the_residue_and_the_conclusions_are_recorded(self):
        w, ev = go()
        self.assertEqual(ev["dream"], {"status": "success", "created": 3, "removed": 1, "peer_card_updated": True})
        self.assertEqual(ev["residue_after_delete"], {"documents": 0, "dream_changes": 0, "peer_card_changes": 1})
        self.assertEqual((ev["conclusions"], ev["dream_runs"]), (2, 1))

    def test_the_messages_are_twenty_fabricated_ones_two_tenants_and_the_fixture_id_in_each(self):
        msgs = pilot.corpus_messages(DOC)
        self.assertEqual(len(msgs), 20)
        self.assertEqual({m["peer_id"] for m in msgs}, {"t-alpha", "t-beta"})
        self.assertTrue(all(m["content"].startswith(m["source_ref"]) and len(m["content"]) <= 300 for m in msgs))
        self.assertLess(len(msgs) * 2, 45)


class Safe(unittest.TestCase):
    def test_the_live_flag_never_runs_a_live_experiment_from_this_file(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = pilot.main(["--live"])
        self.assertIn(code, (2, 3))
        plan = io.StringIO()
        with redirect_stdout(plan):
            self.assertEqual(pilot.main([]), 0)
        self.assertEqual(json.loads(plan.getvalue())["live"], False)

    def test_the_database_reader_only_ever_sends_read_only_statements(self):
        sent = []

        class Done:
            returncode = 0

            def __init__(self, out):
                self.stdout = out

        def fake(cmd, input=None, **kw):
            sent.append(input)
            return Done(
                "BEGIN\n0|1|1\nROLLBACK\n"
                if "dream_run_changes" in input
                else "BEGIN\n3|2026-11-26 07:42:26|abc\nROLLBACK\n"
            )

        db = pilot.OracleDb(run=fake)
        db.personal_state()
        db.residue("um-t2-fixture-v1", "c1")
        for text in sent:
            self.assertTrue(text.startswith("BEGIN READ ONLY;") and text.rstrip().endswith("ROLLBACK;"))
            self.assertIsNone(re.search(r"\b(insert|update|delete|drop|alter|create|truncate)\b", text, re.I))

    def test_no_token_or_credential_value_appears_in_the_sources(self):
        for name in ("um_honcho_pilot", "um_honcho_client", "um_budget_guard", "um_budget"):
            text = (ROOT / "tools" / f"{name}.py").read_text()
            self.assertIsNone(re.search(r"eyJ[A-Za-z0-9_-]{15,}|Bearer [A-Za-z0-9._-]{20,}|neo4j\+s://", text), name)


if __name__ == "__main__":
    unittest.main()
