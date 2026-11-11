"""LIFE-576 option (a): the Oracle path, with fake tunnel, browser and pages. Every way it can fail must hand the work back to the runner's own browser."""
import asyncio
import unittest

from lifeos.jobs.resolve.aggregators import jobright_oracle as jo

ON = {"JOBRIGHT_ON_ORACLE": "on", "LIFEOS_BROWSER_SSH_KEY": "k", "LIFEOS_ACQ_SSH_JUMP_HOST": "h", "LIFEOS_ACQ_SSH_JUMP_KNOWN_HOSTS": "h ssh-ed25519 AAAA"}


class Page:
    def __init__(self, url="about:blank", signed_in=True):
        self.url, self.signed_in, self.closed = url, signed_in, False

    async def goto(self, *a, **k):
        if not self.signed_in:
            return
        self.url = "https://jobright.ai/"

    def locator(self, *a):
        page = self

        class L:
            first = property(lambda s: s)

            async def wait_for(self, timeout=0):
                if not page.signed_in:
                    raise TimeoutError("no Profile")
        return L()

    async def wait_for_timeout(self, ms):
        pass

    async def close(self):
        self.closed = True


class Context:
    def __init__(self, pages):
        self.pages = pages

    async def new_page(self):
        page = Page(signed_in=self.signed_in)
        self.pages.append(page)
        return page


class Browser:
    def __init__(self, pages=(), signed_in=True):
        self.contexts = [Context(list(pages))]
        self.contexts[0].signed_in = signed_in
        self.closed = False

    async def close(self):
        self.closed = True


class Chromium:
    def __init__(self, browser=None, fail=False):
        self.browser, self.fail, self.url = browser, fail, None

    async def connect_over_cdp(self, url, timeout=0):
        self.url = url
        if self.fail:
            raise OSError("refused")
        return self.browser


class PW:
    def __init__(self, **kw):
        self.chromium = Chromium(**kw)


class FakeTunnel:
    raise_reason = None

    def __init__(self, environ):
        pass

    def __enter__(self):
        if FakeTunnel.raise_reason:
            raise jo.Unreachable(FakeTunnel.raise_reason)
        self.port = 5555
        return self

    def __exit__(self, *a):
        return False


def attempt(pw, env=ON, follow=None, urls=("u1", "u2")):
    async def default(context, page, url):
        return {"outcome": "landed", "url": url}
    return asyncio.run(jo.attempt(pw, list(urls), env, follow or default, 0, tunnel=FakeTunnel))


class Route(unittest.TestCase):
    def setUp(self):
        FakeTunnel.raise_reason = None

    def test_the_switch_must_say_on(self):
        for value in (None, "", "off", "no", "1"):
            env = dict(ON)
            if value is None:
                del env["JOBRIGHT_ON_ORACLE"]
            else:
                env["JOBRIGHT_ON_ORACLE"] = value
            self.assertEqual(attempt(PW(browser=Browser()), env), (None, "off"))

    def test_the_off_path_never_touches_the_browser_or_the_tunnel(self):
        pw = PW(browser=Browser())
        env = {**ON, "JOBRIGHT_ON_ORACLE": "off"}
        self.assertEqual(attempt(pw, env), (None, "off"))
        self.assertIsNone(pw.chromium.url)

    def test_a_signed_in_idle_browser_does_the_work_and_is_disconnected(self):
        browser = Browser()
        results, reason = attempt(PW(browser=browser))
        self.assertEqual((reason, [r["url"] for r in results]), ("ok", ["u1", "u2"]))
        self.assertTrue(browser.closed)
        self.assertTrue(browser.contexts[0].pages[-1].closed)                              # our own tab is closed, nothing else touched

    def test_the_connection_goes_through_the_local_end_of_the_tunnel_only(self):
        pw = PW(browser=Browser())
        attempt(pw)
        self.assertEqual(pw.chromium.url, "http://127.0.0.1:5555")

    def test_no_key_or_host_falls_back(self):
        for reason in ("no_key", "no_host", "unreachable"):
            FakeTunnel.raise_reason = reason
            self.assertEqual(attempt(PW(browser=Browser())), (None, reason))

    def test_a_browser_that_refuses_the_connection_falls_back(self):
        self.assertEqual(attempt(PW(fail=True)), (None, "unreachable"))

    def test_a_page_someone_else_has_open_means_busy(self):
        self.assertEqual(attempt(PW(browser=Browser(pages=[Page("https://jobright.ai/jobs/info/9")]))), (None, "busy"))

    def test_a_blank_tab_is_not_busy(self):
        results, reason = attempt(PW(browser=Browser(pages=[Page("about:blank")])))
        self.assertEqual(reason, "ok")

    def test_a_signed_out_session_falls_back_instead_of_failing(self):
        browser = Browser(signed_in=False)
        self.assertEqual(attempt(PW(browser=browser)), (None, "signed_out"))
        self.assertTrue(browser.closed)

    def test_a_site_failure_on_one_job_is_counted_like_the_runner_path_does(self):
        async def follow(context, page, url):
            if url == "u1":
                raise RuntimeError("site broke")
            return {"outcome": "landed"}
        results, reason = attempt(PW(browser=Browser()), follow=follow)
        self.assertEqual((reason, [r["outcome"] for r in results]), ("ok", ["chain_failed", "landed"]))

    def test_a_lost_browser_part_way_hands_everything_back(self):
        async def follow(context, page, url):
            raise RuntimeError("Target page, context or browser has been closed")
        self.assertEqual(attempt(PW(browser=Browser()), follow=follow), (None, "lost"))


class Command(unittest.TestCase):
    def test_the_forward_opens_only_the_browser_port_on_the_loopback_with_a_checked_host(self):
        cmd = jo.ssh_command("/k", "/h", "100.1.1.1", 4444)
        self.assertIn("127.0.0.1:4444:127.0.0.1:9223", cmd)
        self.assertIn("v7-browser-fwd@100.1.1.1", cmd)
        self.assertIn("StrictHostKeyChecking=yes", cmd)
        self.assertEqual(cmd[:2], ["ssh", "-N"])
        self.assertFalse([c for c in cmd if c.startswith("0.0.0.0") or c == "-A"])


class TunnelTests(unittest.TestCase):
    def test_a_missing_key_never_spawns_anything(self):
        spawned = []
        env = {k: v for k, v in ON.items() if k != "LIFEOS_BROWSER_SSH_KEY"}
        with self.assertRaises(jo.Unreachable) as caught:
            jo.Tunnel(env, spawn=lambda *a, **k: spawned.append(a)).__enter__()
        self.assertEqual((str(caught.exception), spawned), ("no_key", []))

    def test_a_ssh_process_that_exits_at_once_reads_as_unreachable_and_leaves_no_files(self):
        class Dead:
            def poll(self):
                return 255

            def terminate(self):
                pass
        t = jo.Tunnel(ON, spawn=lambda *a, **k: Dead(), wait=2)
        with self.assertRaises(jo.Unreachable) as caught:
            t.__enter__()
        self.assertEqual(str(caught.exception), "unreachable")
        import os
        self.assertFalse(os.path.exists(t.dir.name))


if __name__ == "__main__":
    unittest.main()
