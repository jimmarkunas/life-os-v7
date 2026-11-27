"""(synthetic): Todoist (Personal only) and Asana (Bytalos programme work only) are read-only, scoped, and fail alone
"""
import ast
import unittest
from pathlib import Path

from lifeos.inbox import inbox, sources
from lifeos.inbox.model import InboxError
from tests.inbox import fixtures as F

APPROVED, TGP = "p-prog", "p-client"
FORBIDDEN = ("complete_task", "close_task", "add_task", "update_task", "delete_task", "create_task", "post", "put", "request", "update", "delete", "insert", "TodoistAPI", "Client", "from_env")


def task(id="t1", project="proj-a", completed=False, due=None):
    return {"id": id, "url": f"https://todo.example.test/{id}", "content": f"{F.SAME_TITLE} {F.CANARY}", "is_completed": completed, "project_id": project, "created_at": "2026-11-14T13:30:00Z",
            "due": due}


def asana(gid="a1", projects=(APPROVED,), assignee="u-jim", completed=False):
    return {"gid": gid, "permalink_url": f"https://work.example.test/{gid}", "name": f"{F.SAME_TITLE} {F.CANARY}", "completed": completed, "assignee": {"gid": assignee} if assignee else None,
            "projects": [{"gid": p} for p in projects], "modified_at": "2026-11-14T19:30:00.000Z", "due_on": "2026-11-16"}


def opener(rows, error=None):
    def open_reader(credential_ref):
        def read(*args):
            if error:
                raise error
            return rows
        return read
    return open_reader


def todoist(workspace="personal", rows=None, error=None, projects=("proj-a",)):
    return sources.TodoistSource(workspace, f"jim-{workspace}", "jim", projects, open_reader=opener([task()] if rows is None else rows, error))


def asana_src(workspace="bytalos", rows=None, error=None):
    return sources.AsanaSource(workspace, f"jim-{workspace}", "u-jim", (APPROVED,), open_reader=opener([asana()] if rows is None else rows, error))


def run(srcs, **kw):
    return inbox.build_inbox(srcs, F.principal(sources=("todoist", "asana"), **kw), F.NOW)


class TodoistReads(unittest.TestCase):
    def test_selected_personal_tasks_normalize_and_other_projects_stay_out(self):
        result = run([todoist(rows=[task("t1"), task("t2", project="proj-other"), task("t3", due={"datetime": "2026-11-14T09:00:00", "timezone": "America/Chicago"})])])
        self.assertEqual([h.state for h in result.health], ["ok"])
        self.assertEqual(sorted(r.native_id for r in result.records), ["t1", "t3"])
        one = next(r for r in result.records if r.native_id == "t3")
        self.assertEqual((one.record_id, one.url, one.source_status, one.due_tz, one.presentation, one.flags), ("todoist:personal:jim-personal:t3", "https://todo.example.test/t3", "open", "America/Chicago",
                                                                                                                  "needs_me", frozenset({"personal_task"})))
        self.assertEqual(one.retrieved_at, F.NOW)

    def test_a_completed_task_shows_the_source_word_and_is_never_inferred(self):
        self.assertEqual(run([todoist(rows=[task(completed=True)])]).records[0].source_status, "completed")

    def test_todoist_is_personal_only(self):
        seen = []
        bytalos = todoist("bytalos")
        bytalos._open = lambda ref: seen.append(ref)
        result = run([bytalos])
        self.assertEqual((seen, result.records, [(h.state, h.reason) for h in result.health]), ([], (), [("unavailable", "todoist_personal_only")]))


class AsanaReads(unittest.TestCase):
    def test_approved_programme_tasks_show_and_my_tasks_need_me(self):
        result = run([asana_src(rows=[asana("a1"), asana("a2", assignee="someone-else"), asana("a3", assignee=None)])])
        self.assertEqual([h.state for h in result.health], ["ok"])
        by_id = {r.native_id: r for r in result.records}
        self.assertEqual(sorted(by_id), ["a1", "a2", "a3"])
        self.assertEqual((by_id["a1"].record_id, by_id["a1"].presentation, by_id["a2"].presentation), ("asana:bytalos:jim-bytalos:a1", "needs_me", "upcoming"))
        self.assertEqual(by_id["a1"].due_tz, "America/Chicago")

    def test_tgp_and_client_projects_never_show(self):
        rows = [asana("only-client", projects=(TGP,)), asana("mixed", projects=(APPROVED, TGP)), asana("none", projects=()), asana("ok")]
        result = run([asana_src(rows=rows)])
        self.assertEqual([r.native_id for r in result.records], ["ok"])  # the client-only task is out of scope
        self.assertEqual([(h.state, h.rejected) for h in result.health], [("degraded", 2)])
        self.assertNotIn(TGP, repr(result))

    def test_a_completed_task_is_left_out(self):
        self.assertEqual(run([asana_src(rows=[asana(completed=True)])]).records, ())

    def test_asana_is_bytalos_only(self):
        personal = asana_src("personal")
        result = run([personal])
        self.assertEqual(([(h.state, h.reason) for h in result.health], result.records), ([("unavailable", "asana_bytalos_only")], ()))


class FailClosed(unittest.TestCase):
    def test_missing_provider_permission_is_blocked_health_not_a_fake_success(self):
        result = run([todoist(error=InboxError("integration_blocked")), asana_src()])
        health = {h.source: (h.state, h.reason) for h in result.health}
        self.assertEqual(health, {"asana": ("ok", "ok"), "todoist": ("unavailable", "integration_blocked")})
        self.assertEqual([r.source for r in result.records], ["asana"])

    def test_an_erroring_source_hides_its_message_and_the_other_workspace_survives(self):
        result = run([todoist(error=TimeoutError(F.ERROR_CANARY)), asana_src()])
        self.assertEqual(next(h.reason for h in result.health if h.source == "todoist"), "timeouterror")
        self.assertNotIn(F.ERROR_CANARY, repr(result.health))
        self.assertEqual(len(result.records), 1)

    def test_a_missing_or_revoked_grant_reads_nothing(self):
        for kw in ({"workspaces": ("bytalos",)}, {"revoked": (("todoist", "personal"),)}):
            with self.subTest(kw):
                source, seen = todoist(), []
                source._open = lambda ref, seen=seen: seen.append(ref)
                self.assertEqual(run([source], **kw).health[0].state, "no_grant")
                self.assertEqual(seen, [])

    def test_the_log_safe_health_carries_no_record_text(self):
        result = run([todoist(), asana_src()])
        self.assertNotIn(F.CANARY, repr(result.health))


class ReadOnly(unittest.TestCase):
    def test_the_module_names_no_write_and_builds_no_client(self):
        tree = ast.parse(Path(sources.__file__).read_text())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertEqual(names & set(FORBIDDEN), set())

    def test_the_default_reader_refuses_and_nothing_live_is_reachable(self):
        result = run([sources.TodoistSource("personal", "jim-personal", "jim", ("proj-a",)), sources.AsanaSource("bytalos", "jim-bytalos", "u-jim", (APPROVED,))])
        self.assertEqual([(h.state, h.reason) for h in result.health], [("unavailable", "live_reads_not_authorized")] * 2)
        self.assertEqual(result.records, ())


if __name__ == "__main__":
    unittest.main()
