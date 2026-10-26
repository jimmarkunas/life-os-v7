"""LIFE-75: the Alert settings screen on the Close service: reads and saves repository variables through a signed review and confirm, and the saved values are exactly what the senders read."""
import datetime as dt
import json
import re
import unittest
import urllib.error
from unittest import mock

from lifeos.platform import alert_registry as registry, alert_settings as settings, alerts
from lifeos.sources import alerts_app as app, close_app as C

LOGIN = "jim@example.com"
SECRET = b"s" * 32
NOW = dt.datetime(2026, 10, 26, 9, 0, tzinfo=app.CENTRAL)


class GitHub:
    """A fake of the repository-variables API: GET one, PATCH one (404 when absent), POST to create."""

    def __init__(self, variables=None, fail=False, drop_writes=False):
        self.vars, self.fail, self.drop, self.log = dict(variables or {}), fail, drop_writes, []

    def __call__(self, request, timeout=None):
        self.log.append((request.get_method(), request.full_url.split("/variables")[1], request.get_header("Authorization")))
        if self.fail:
            raise OSError("down")
        name = request.full_url.rsplit("/variables", 1)[1].lstrip("/")
        body = json.loads(request.data) if request.data else None
        method = request.get_method()
        if method == "GET":
            return self.reply({"name": name, "value": self.vars[name]}) if name in self.vars else self.missing(request)
        if method == "PATCH":
            if name not in self.vars:
                return self.missing(request)
            if not self.drop:
                self.vars[name] = body["value"]
            return self.reply({})
        if method == "POST":
            if not self.drop:
                self.vars[body["name"]] = body["value"]
            return self.reply({})
        raise AssertionError(method)

    def missing(self, request):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    @staticmethod
    def reply(data):
        return mock.MagicMock(__enter__=lambda s: mock.Mock(read=lambda: json.dumps(data).encode()), __exit__=lambda *a: False)


def secrets(token=b"github_pat_synthetic"):
    return {"link": SECRET, "ghvars": token} if token else {"link": SECRET}


def request(method="GET", form=None, path="/alerts"):
    return {"method": method, "path": path, "query": {}, "headers": {}, "body": b"", "form": form or {}}


def page_text(result):
    return result[2].decode()


class Screen(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(C, "_now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gh = GitHub()

    def get(self, **kw):
        return app.handle(request(), {"login": LOGIN}, secrets(**kw), self.gh)

    def post(self, form, **kw):
        return app.handle(request("POST", form), {"login": LOGIN}, secrets(**kw), self.gh)

    def fields(self, html):
        return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', html))

    def propose(self, enabled=True, mute="", edit=None):
        """Open the screen, build the form a browser would post, and return it with the review token step done."""
        first = page_text(self.get())
        hidden = self.fields(first)
        form = dict(hidden)
        if enabled:
            form["enabled"] = "1"
        form["mute"] = mute
        for key, spec in registry.REGISTRY.items():
            for c in spec.channels:
                form[f"ch:{key}:{c}"] = "1"
        for name, value in (edit or {}).items():
            if value is None:
                form.pop(name, None)
            else:
                form[name] = value
        return {**form, "step": "review"}

    def test_the_screen_shows_the_switch_the_mute_and_every_registered_alert_with_its_defaults(self):
        status, ctype, body, headers = self.get()
        text = body.decode()
        self.assertEqual((status, ctype), (200, "text/html; charset=utf-8"))
        self.assertIn('/alerts.css', text)
        self.assertNotIn('name="enabled" value="1" checked', text)                           # nothing saved: alerts OFF
        for key, spec in registry.REGISTRY.items():
            with self.subTest(key=key):
                self.assertIn(f'name="ch:{key}:telegram"', text)
        self.assertIn('name="ch:run_failed:ntfy" value="1" aria-label="Phone for run_failed" checked disabled>', text)     # a PAGE alert's phone box is fixed on
        self.assertIn('name="ch:quiet_24h:ntfy" value="1" aria-label="Phone for quiet_24h" checked', text)
        self.assertNotRegex(text, r'name="ch:quiet_24h:telegram" value="1" aria-label="Telegram for quiet_24h" checked')
        self.assertNotIn("github_pat", text)

    def test_without_the_credential_the_screen_says_so_and_changes_nothing(self):
        for result in (self.get(token=b""), self.post({"step": "review"}, token=b"")):
            self.assertEqual(result[0], 503)
            self.assertIn("not installed", page_text(result))
        self.assertEqual(self.gh.log, [])

    def test_a_github_failure_is_a_fixed_message_with_no_detail(self):
        result = app.handle(request(), {"login": LOGIN}, secrets(), GitHub(fail=True))
        self.assertEqual(result[0], 503)
        self.assertNotIn("github_pat", page_text(result))
        self.assertNotIn("down", page_text(result))

    def test_a_saved_state_is_shown_back(self):
        self.gh.vars = {"ALERTS_ENABLED": "on", "ALERTS_MUTED_UNTIL": "2026-10-27T08:00:00-05:00", "ALERT_SETTINGS": json.dumps({"v": 1, "channels": {"quiet_24h": ["ntfy", "telegram"]}})}
        text = page_text(self.get())
        self.assertIn('name="enabled" value="1" checked', text)
        self.assertIn('value="2026-10-27T08:00"', text)
        self.assertRegex(text, r'name="ch:quiet_24h:telegram" value="1" aria-label="Telegram for quiet_24h" checked')

    def test_a_past_mute_is_shown_empty(self):
        self.gh.vars = {"ALERTS_MUTED_UNTIL": "2026-10-25T08:00:00-05:00"}
        self.assertIn('name="mute" value=""', page_text(self.get()))

    def test_review_lists_only_what_would_change_and_saves_nothing(self):
        form = self.propose(enabled=True, mute="2026-10-27T08:00", edit={"ch:quiet_24h:telegram": "1"})
        result = self.post(form)
        text = page_text(result)
        self.assertEqual(result[0], 200)
        self.assertIn("Alerts will be turned ON", text)
        self.assertIn("Mute until 2026-10-27 08:00 Central.", text)
        self.assertIn("quiet 24h: Phone, Telegram", text)
        self.assertNotIn("run failed:", text)
        self.assertEqual([m for m, *_ in self.gh.log if m != "GET"], [])                       # review never writes

    def test_confirm_saves_the_three_variables_and_reports_only_after_they_read_back(self):
        review = self.post(self.propose(enabled=True, mute="2026-10-27T08:00", edit={"ch:quiet_24h:telegram": "1", "ch:stuck_jobs:ntfy": None}))
        result = self.post({**self.fields(page_text(review)), "step": "confirm"})
        self.assertEqual(result[0], 200, page_text(result))
        self.assertIn("Alert settings saved", page_text(result))
        self.assertEqual(self.gh.vars["ALERTS_ENABLED"], "on")
        self.assertEqual(self.gh.vars["ALERTS_MUTED_UNTIL"], "2026-10-27T08:00:00-05:00")
        self.assertEqual(json.loads(self.gh.vars["ALERT_SETTINGS"]), {"v": 1, "channels": {"quiet_24h": ["ntfy", "telegram"], "stuck_jobs": []}})
        self.assertTrue(all(auth == "Bearer github_pat_synthetic" for _, _, auth in self.gh.log))

    def test_what_is_saved_is_exactly_what_the_senders_read(self):
        review = self.post(self.propose(enabled=True, mute="2026-10-27T08:00", edit={"ch:quiet_24h:telegram": "1", "ch:run_failed:telegram": None}))
        self.post({**self.fields(page_text(review)), "step": "confirm"})
        env = dict(self.gh.vars)
        self.assertTrue(settings.enabled(env))
        self.assertEqual(settings.channels("quiet_24h", env), ("ntfy", "telegram"))
        self.assertEqual(settings.channels("run_failed", env), ("ntfy", "whatsapp"))           # Telegram unticked, the phone kept
        self.assertTrue(alerts.muted(env, dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc)))        # 7 AM Central, before the 8 AM mute ends
        self.assertFalse(alerts.muted(env, dt.datetime(2026, 10, 27, 14, 0, tzinfo=dt.timezone.utc)))       # 9 AM Central, after

    def test_the_pages_carry_no_inline_style_or_script_the_services_policy_would_block(self):
        for html in (page_text(self.get()), page_text(self.post(self.propose(enabled=True)))):
            self.assertNotRegex(html, r'\sstyle=|<style|\son[a-z]+=|<script(?![^>]*\ssrc=)')

    def test_no_change_is_reviewed_as_no_change_and_save_is_disabled(self):
        text = page_text(self.post(self.propose(enabled=False)))
        self.assertIn("No change.", text)
        self.assertIn('value="confirm" disabled', text)

    def test_a_forged_expired_or_tampered_form_is_refused_and_nothing_is_written(self):
        good = self.propose(enabled=True)
        for name, form in (("forged token", {**good, "t": "0" * 64}), ("expired", {**good, "exp": "1"}), ("bad mute", {**good, "mute": "tomorrow"}), ("no step", {**good, "step": "x"})):
            with self.subTest(name):
                result = self.post(form)
                self.assertEqual(result[0], 403)
        review = self.post(good)
        confirm = {**self.fields(page_text(review)), "enabled": "1", "step": "confirm"}
        confirm.pop("enabled")                                                                  # the confirm no longer matches what was reviewed
        tampered = self.post(confirm)
        self.assertEqual(tampered[0], 403)
        self.assertEqual([m for m, *_ in self.gh.log if m != "GET"], [])

    def test_cancel_writes_nothing(self):
        result = self.post({**self.propose(enabled=True), "step": "cancel"})
        self.assertEqual(result[0], 200)
        self.assertIn("Master switches", page_text(result))                                     # back on the settings screen, not an end page
        self.assertNotIn("Cancelled", page_text(result))
        self.assertEqual([m for m, *_ in self.gh.log if m != "GET"], [])

    def test_a_save_that_does_not_read_back_is_reported_as_degraded(self):
        self.gh = GitHub(drop_writes=True)
        review = self.post(self.propose(enabled=True))
        result = self.post({**self.fields(page_text(review)), "step": "confirm"})
        self.assertEqual(result[0], 500)
        self.assertIn("Could not verify the save", page_text(result))
        self.assertNotIn("Alert settings saved", page_text(result))

    def test_the_stylesheet_is_served_and_the_service_routes_both_paths(self):
        self.assertEqual(app.handle_css(request(path="/alerts.css"), {}, {})[:2], (200, "text/css; charset=utf-8"))
        for path, name in (("/alerts", "handle"), ("/alerts.css", "handle_css")):
            self.assertEqual(C.ROUTES[path], ("alerts_app", name))
        status, ctype, body, _ = C.handle(request(path="/alerts.css"), {"login": LOGIN}, {})
        self.assertEqual((status, ctype), (200, "text/css; charset=utf-8"))
        self.assertIn(".sw input:checked", body.decode())

    def test_the_release_closure_carries_the_screen_and_what_it_imports(self):
        for path in ("lifeos/sources/alerts_app.py", "lifeos/platform/alerts.py", "lifeos/platform/alert_registry.py", "lifeos/platform/alert_settings.py", "lifeos/platform/redact.py"):
            self.assertIn(path, C.CLOSURE)


if __name__ == "__main__":
    unittest.main()
