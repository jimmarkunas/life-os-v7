import inspect
import json
import unittest
import urllib.error

from lifeos.platform import todoist_governed as g

RW = "w" * 20
RD = "r" * 20


class Fake:
    """A synthetic Todoist API v1: tasks, comments, projects; records every request; can fail a given path."""

    def __init__(self, tasks=None, fail=None):
        self.tasks = {str(t["id"]): dict(t) for t in (tasks or [])}
        self.comments, self.calls, self.fail, self.n = [], [], fail or {}, 100

    def __call__(self, request, timeout=30):
        method, url = request.get_method(), request.full_url
        path = url.split("/api/v1", 1)[1].split("?")[0]
        body = json.loads(request.data) if request.data else None
        auth = request.get_header("Authorization")
        self.calls.append((method, path, auth, body))
        if path in self.fail:
            raise urllib.error.HTTPError(url, self.fail[path], "x", {}, None)
        out = self.route(method, path, body, url)
        return Resp(out)

    def route(self, method, path, body, url):
        if method == "GET" and path == "/tasks":
            project = "project_id=" in url and url.split("project_id=")[1].split("&")[0]
            return {
                "results": [
                    t for t in self.tasks.values() if not project or t["project_id"] == project and not t.get("checked")
                ],
                "next_cursor": None,
            }
        if method == "GET" and path == "/projects":
            return {"results": [{"id": "p1", "name": "To Do List"}], "next_cursor": None}
        if method == "GET" and path == "/comments":
            return {"results": list(self.comments), "next_cursor": None}
        if method == "GET" and path.startswith("/tasks/"):
            task = self.tasks.get(path.split("/")[2])
            if not task:
                raise urllib.error.HTTPError("u", 404, "x", {}, None)
            return task
        if method == "POST" and path == "/tasks":
            self.n += 1
            self.tasks[str(self.n)] = {"id": str(self.n), "checked": False, **body}
            return {"id": str(self.n)}
        if method == "POST" and path == "/comments":
            self.n += 1
            self.comments.append({"id": str(self.n), **body})
            return {"id": str(self.n)}
        parts = path.split("/")
        task = self.tasks[parts[2]]
        if len(parts) == 3:
            task.update(body)
        elif parts[3] == "move":
            task["project_id"] = body["project_id"]
        elif parts[3] == "close":
            task["checked"] = True
        elif parts[3] == "reopen":
            task["checked"] = False
        return {}


class Resp:
    def __init__(self, out):
        self.raw = json.dumps(out).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.raw


def state(fake, enabled=True, **kw):
    return {
        "read_token": RD,
        "write_token": RW,
        "writes_enabled": enabled,
        "allowed_projects": {"p1"},
        "opener": fake,
        **kw,
    }


def code(op, args, st):
    try:
        g.run(op, args, st)
    except g.GovernedError as e:
        return str(e)
    return None


class Governed(unittest.TestCase):
    def test_reads_use_the_read_token_and_work_with_writes_off(self):
        fake = Fake([{"id": "1", "content": "a", "project_id": "p1"}])
        self.assertEqual(len(g.run("todoist_tasks", {}, state(fake, enabled=False))["tasks"]), 1)
        g.run("todoist_projects", {}, state(fake, enabled=False))
        g.run("todoist_comments", {"task_id": "1"}, state(fake, enabled=False))
        self.assertTrue(all(c[0] == "GET" and c[2] == "Bearer " + RD for c in fake.calls))

    def test_a_refusal_carries_its_fixed_reason_for_the_mcp_to_show(self):
        try:
            g.run("todoist_task_complete", {"task_id": "1"}, state(Fake(), enabled=False))
        except ValueError as error:
            self.assertEqual((str(error), error.reason), ("writes_disabled", "writes_disabled"))
        else:
            self.fail("expected a refusal")

    def test_every_write_is_refused_while_off_and_nothing_is_sent(self):
        fake = Fake([{"id": "1", "content": "a", "project_id": "p1"}])
        for op, args in (
            ("todoist_task_create", {"content": "x", "project_id": "p1"}),
            ("todoist_task_update", {"task_id": "1", "content": "y"}),
            ("todoist_task_move", {"task_id": "1", "project_id": "p1"}),
            ("todoist_task_complete", {"task_id": "1"}),
            ("todoist_task_reopen", {"task_id": "1"}),
            ("todoist_comment_add", {"task_id": "1", "content": "c"}),
        ):
            self.assertEqual(code(op, args, state(fake, enabled=False)), "writes_disabled", op)
        self.assertEqual(fake.calls, [])

    def test_create_is_read_back_goes_to_a_configured_project_and_uses_the_write_token(self):
        fake = Fake()
        out = g.run(
            "todoist_task_create", {"content": " Buy salt ", "project_id": "p1", "due_string": "tomorrow"}, state(fake)
        )
        self.assertEqual(out["verified"], True)
        posts = [c for c in fake.calls if c[0] == "POST"]
        self.assertEqual((len(posts), posts[0][2], posts[0][3]["content"]), (1, "Bearer " + RW, "Buy salt"))
        self.assertEqual(
            code("todoist_task_create", {"content": "z", "project_id": "other"}, state(fake)), "project_not_allowed"
        )
        self.assertEqual(
            code("todoist_task_create", {"content": "z", "project_id": "p1"}, state(fake, allowed_projects=set())),
            "projects_not_configured",
        )

    def test_a_duplicate_is_refused_and_a_replay_never_creates_twice(self):
        fake = Fake()
        g.run("todoist_task_create", {"content": "Buy salt", "project_id": "p1"}, state(fake))
        self.assertEqual(
            code("todoist_task_create", {"content": "buy SALT", "project_id": "p1"}, state(fake)), "duplicate_found"
        )
        self.assertEqual(len([t for t in fake.tasks.values()]), 1)

    def test_simultaneous_identical_creates_make_exactly_one_task(self):
        import threading
        import time

        class Slow(Fake):
            def route(self, method, path, body, url):
                if method == "POST" and path == "/tasks":
                    time.sleep(0.05)  # widen the window between the duplicate check and the create
                return super().route(method, path, body, url)

        fake, results = Slow(), []

        def go():
            results.append(code("todoist_task_create", {"content": "Buy salt", "project_id": "p1"}, state(fake)))

        threads = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual((sorted(r or "ok" for r in results), len(fake.tasks)), (["duplicate_found"] * 3 + ["ok"], 1))

    def test_update_move_complete_reopen_comment_are_each_verified(self):
        fake = Fake([{"id": "1", "content": "a", "project_id": "p1", "checked": False}])
        st = state(fake)
        self.assertTrue(g.run("todoist_task_update", {"task_id": "1", "content": "b"}, st)["verified"])
        self.assertEqual(fake.tasks["1"]["content"], "b")
        self.assertTrue(g.run("todoist_task_move", {"task_id": "1", "project_id": "p1"}, st)["verified"])
        self.assertTrue(g.run("todoist_task_complete", {"task_id": "1"}, st)["verified"])
        self.assertTrue(g.run("todoist_task_reopen", {"task_id": "1"}, st)["verified"])
        out = g.run("todoist_comment_add", {"task_id": "1", "content": "link: J-1"}, st)
        self.assertEqual((out["verified"], len(fake.comments)), (True, 1))

    def test_completion_that_cannot_be_read_back_is_not_called_complete(self):
        class Stuck(Fake):
            def route(self, method, path, body, url):
                if path.endswith("/close"):
                    return {}  # the provider accepted but changed nothing
                return super().route(method, path, body, url)

        fake = Stuck([{"id": "1", "content": "a", "project_id": "p1", "checked": False}])
        self.assertEqual(code("todoist_task_complete", {"task_id": "1"}, state(fake)), "readback_failed")

    def test_update_accepts_only_content_description_and_due_text(self):
        fake = Fake([{"id": "1", "content": "a", "project_id": "p1"}])
        for args in (
            {"task_id": "1", "priority": 4},
            {"task_id": "1"},
            {"task_id": "1", "content": "ok", "labels": ["x"]},
        ):
            self.assertEqual(code("todoist_task_update", args, state(fake)), "bad_fields", args)

    def test_bad_ids_text_and_unknown_operations_are_refused_before_any_call(self):
        fake = Fake()
        self.assertEqual(code("todoist_task_complete", {"task_id": "../tasks"}, state(fake)), "bad_id")
        self.assertEqual(code("todoist_task_create", {"content": "", "project_id": "p1"}, state(fake)), "bad_text")
        self.assertEqual(
            code("todoist_task_create", {"content": "x" * 501, "project_id": "p1"}, state(fake)), "bad_text"
        )
        self.assertEqual(code("todoist_task_delete", {"task_id": "1"}, state(fake)), "unknown_operation")
        self.assertEqual(fake.calls, [])

    def test_provider_errors_are_fixed_codes_and_never_carry_a_token(self):
        fake = Fake([{"id": "1", "content": "a", "project_id": "p1"}], fail={"/tasks/1/close": 403})
        try:
            g.run("todoist_task_complete", {"task_id": "1"}, state(fake))
        except g.GovernedError as e:
            self.assertEqual(str(e), "http_403")
            self.assertNotIn(RW, str(e))

    def test_no_delete_project_or_structure_call_exists_and_the_gate_route_splits_read_from_write(self):
        source = inspect.getsource(g)
        self.assertNotIn('"DELETE"', source)
        self.assertEqual(
            sorted(set(__import__("re").findall(r'"(?:POST|GET)", f?"(/[a-z]+)', source))), ["/comments", "/tasks"]
        )
        self.assertEqual(g.route("todoist_tasks", {}, "personal")[1], "read")
        self.assertEqual(g.route("todoist_task_create", {}, "personal")[1], "write")
        with self.assertRaises(g.GovernedError):
            g.route("todoist_task_delete", {}, "personal")


class FakeBws:
    def __init__(self, store):
        self.store, self.calls, self.envs = store, 0, []

    def __call__(self, argv, env=None, **kw):
        self.calls += 1
        self.envs.append(env)
        item = argv[3]
        out = json.dumps({"id": item, "value": self.store.get(item, "unset")})
        return type("D", (), {"returncode": 0, "stdout": out})()


class Wiring(unittest.TestCase):
    def wired(self, env, store, fake=None):
        bws = FakeBws(store)
        st = g.wire(env, {}, run=bws)
        st["opener"] = fake or Fake([{"id": "1", "content": "a", "project_id": "p1"}])
        return st, bws

    def test_nothing_is_configured_without_the_bitwarden_credential_and_writes_default_off(self):
        st, _ = self.wired({}, {})
        self.assertEqual((st["bws"], st["writes_enabled"], st["allowed_projects"]), (None, False, set()))
        self.assertEqual(code("todoist_tasks", {}, st), "not_configured")
        self.assertEqual(code("todoist_task_complete", {"task_id": "1"}, st), "writes_disabled")

    def test_the_access_tokens_come_from_the_two_alerts_items_are_cached_and_never_in_an_argument(self):
        from lifeos.platform import todoist_refresh as r

        store = {r.APPS["read"]["access"]: RD, r.APPS["write"]["access"]: RW}
        st, bws = self.wired({"BWSALERTS": "0.bwstoken", "LIFEOS_BWS_BIN": "bws"}, store)
        g.run("todoist_tasks", {}, st)
        g.run("todoist_projects", {}, st)
        self.assertEqual(bws.calls, 2)  # one read per app, then cached
        self.assertTrue(all(e["BWS_ACCESS_TOKEN"] == "0.bwstoken" for e in bws.envs))
        self.assertTrue(all(c[2] == "Bearer " + RD for c in st["opener"].calls))

    def test_an_unset_or_empty_access_item_is_not_configured_never_a_guess(self):
        from lifeos.platform import todoist_refresh as r

        st, _ = self.wired({"BWSALERTS": "t"}, {r.APPS["read"]["access"]: "unset", r.APPS["write"]["access"]: ""})
        self.assertEqual(code("todoist_tasks", {}, st), "not_configured")

    def test_writes_need_the_switch_exactly_on_and_a_project_list(self):
        from lifeos.platform import todoist_refresh as r

        store = {r.APPS["read"]["access"]: RD, r.APPS["write"]["access"]: RW}
        for value in ("", "true", "ON", "yes"):
            st, _ = self.wired({"BWSALERTS": "t", "TODOIST_WRITES": value, "TODOIST_ALLOWED_PROJECTS": "p1"}, store)
            self.assertEqual(
                code("todoist_task_create", {"content": "x", "project_id": "p1"}, st), "writes_disabled", value
            )
        st, _ = self.wired({"BWSALERTS": "t", "TODOIST_WRITES": "on"}, store)
        self.assertEqual(
            code("todoist_task_create", {"content": "x", "project_id": "p1"}, st), "projects_not_configured"
        )
        st, _ = self.wired(
            {"BWSALERTS": "t", "TODOIST_WRITES": "on", "TODOIST_ALLOWED_PROJECTS": " p1 , p2 "}, store, Fake()
        )
        self.assertEqual(st["allowed_projects"], {"p1", "p2"})
        self.assertTrue(g.run("todoist_task_create", {"content": "x", "project_id": "p1"}, st)["verified"])


if __name__ == "__main__":
    unittest.main()
