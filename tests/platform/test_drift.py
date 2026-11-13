"""LIFE-528 step 0: the deploy drift check. UNKNOWN is never DEPLOYED; a changed, missing or stopped surface is DRIFT; one quiet incident opens and closes."""
import json
import unittest
from pathlib import Path

from lifeos.edge.ticker import ticker
from lifeos.platform import drift

GOOD = b"print('installed')\n"


def surfaces(n=2):
    return tuple(drift.Surface("s%d" % i, "repo/f%d.py" % i, "/opt/f%d.py" % i, "u%d.timer" % i) for i in range(n))


class Classify(unittest.TestCase):
    def test_matching_hash_and_a_running_unit_is_deployed(self):
        self.assertEqual(drift.classify(drift.blob_sha(GOOD), GOOD, "active"), drift.DEPLOYED)

    def test_blob_sha_is_gits_object_id(self):
        self.assertEqual(drift.blob_sha(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")             # git hash-object of an empty file
        self.assertEqual(drift.blob_sha(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")

    def test_a_different_hash_a_missing_file_or_a_stopped_unit_is_drift(self):
        sha = drift.blob_sha(GOOD)
        self.assertEqual(drift.classify(sha, GOOD + b"x", "active"), drift.DRIFT)
        self.assertEqual(drift.classify(sha, drift.MISSING, "active"), drift.DRIFT)
        self.assertEqual(drift.classify(sha, GOOD, "inactive"), drift.DRIFT)
        self.assertEqual(drift.classify(sha, GOOD, "failed"), drift.DRIFT)

    def test_anything_unreadable_is_unknown_and_never_deployed(self):
        sha = drift.blob_sha(GOOD)
        self.assertEqual(drift.classify(None, GOOD, "active"), drift.UNKNOWN)        # main could not be read
        self.assertEqual(drift.classify(sha, None, "active"), drift.UNKNOWN)         # the installed file could not be read
        self.assertEqual(drift.classify(sha, GOOD, None), drift.UNKNOWN)             # systemd could not be asked
        for expected in (None, sha):
            for installed in (None, GOOD, drift.MISSING):
                for unit in (None, "active", "inactive"):
                    if expected is None or installed is None or unit is None:
                        self.assertNotEqual(drift.classify(expected, installed, unit), drift.DEPLOYED)


class Check(unittest.TestCase):
    def test_one_row_per_surface_with_hash_prefix_and_unit_state_and_no_content(self):
        rows = drift.check(lambda p: drift.blob_sha(GOOD), lambda p: GOOD, lambda u: "active", surfaces())
        self.assertEqual([r["state"] for r in rows], [drift.DEPLOYED] * 2)
        self.assertEqual(rows[0]["installed_hash"], drift.blob_sha(GOOD)[:12])
        self.assertNotIn("installed", json.dumps(rows).replace("installed_hash", ""))                 # no file content anywhere in the rows

    def test_a_reader_that_raises_reads_as_unknown(self):
        def boom(_):
            raise RuntimeError("secret detail")
        for kwargs in ({"expected_for": boom}, {"read": boom}, {"unit_state_for": boom}):
            args = {"expected_for": lambda p: drift.blob_sha(GOOD), "read": lambda p: GOOD, "unit_state_for": lambda u: "active", **kwargs}
            rows = drift.check(surfaces=surfaces(1), **args)
            self.assertEqual(rows[0]["state"], drift.UNKNOWN)
            self.assertNotIn("secret", json.dumps(rows))

    def test_every_installer_under_lifeos_edge_is_covered_by_a_surface_or_named_in_the_allowlist_with_a_reason(self):
        """LIFE-604 item 4: nothing installed on the VM may go unwatched by accident."""
        scripts = sorted(str(p) for p in Path("lifeos/edge").rglob("install-*.sh"))
        self.assertGreaterEqual(len(scripts), 4)                                     # the four known installers: ticker, off-site backup, Close, runner
        self.assertEqual(drift.uncovered_installers(scripts), [])
        for path, reason in drift.UNCOVERED_INSTALLERS.items():
            self.assertTrue(Path(path).is_file(), path)                              # an allowlist entry for a script that no longer exists is stale
            self.assertGreater(len(reason.split()), 5, path)                         # a reason, not a placeholder
            self.assertNotIn(path, {s.repo_path for s in drift.SURFACES}, path)      # covered AND allowlisted is a contradiction

    def test_a_new_installer_with_no_surface_and_no_allowlist_entry_is_reported(self):
        scripts = ["lifeos/edge/ticker/oracle/install-ticker.sh", "lifeos/edge/newthing/install-newthing.sh"]
        self.assertEqual(drift.uncovered_installers(scripts), ["lifeos/edge/newthing/install-newthing.sh"])

    def test_every_surface_names_a_real_repo_file(self):
        for surface in drift.SURFACES:
            self.assertTrue(Path(surface.repo_path).is_file(), surface.repo_path)

    def test_the_close_release_script_the_updater_and_their_timer_are_watched(self):
        by_name = {s.name: s for s in drift.SURFACES}
        for name, installed in (("close/close_release.py", "/opt/lifeos-close/bin/close_release.py"), ("close/install-close.sh", "/opt/lifeos-close/bin/install-close.sh")):
            self.assertEqual((by_name[name].installed, by_name[name].unit), (installed, "lifeos-close-release.timer"))
        self.assertEqual(by_name["close/close_release.py"].repo_path, "lifeos/edge/oracle/close_release.py")


class Signal(unittest.TestCase):
    def rows(self, *states):
        return [{"surface": "s%d" % i, "state": s} for i, s in enumerate(states)]

    def test_open_on_any_drift_close_only_when_all_deployed_unknown_changes_nothing(self):
        self.assertIs(drift.incident_signal(self.rows("DEPLOYED", "DRIFT")), True)
        self.assertIs(drift.incident_signal(self.rows("UNKNOWN", "DRIFT")), True)
        self.assertIs(drift.incident_signal(self.rows("DEPLOYED", "DEPLOYED")), False)
        self.assertIsNone(drift.incident_signal(self.rows("DEPLOYED", "UNKNOWN")))
        self.assertIsNone(drift.incident_signal(self.rows("UNKNOWN")))

    def test_summary_counts_and_names_the_drifted_surfaces(self):
        counts, drifted = drift.summary(self.rows("DEPLOYED", "DRIFT", "UNKNOWN", "DRIFT"))
        self.assertEqual(counts, {"DEPLOYED": 1, "DRIFT": 2, "UNKNOWN": 1})
        self.assertEqual(drifted, ["s1", "s3"])


class TickerIncident(unittest.TestCase):
    def run_it(self, rows):
        calls = []
        ticker.drift_incident("t", sync=lambda *a: calls.append(a), rows=rows)
        return calls

    def test_drift_opens_one_quiet_incident_with_the_registered_keys(self):
        calls = self.run_it([{"surface": "ticker/alerts.py", "state": "DRIFT"}])
        self.assertEqual(len(calls), 1)
        label, title, body, signal, open_push, close_push = calls[0]
        self.assertEqual((label, signal), ("deploy-drift", True))
        self.assertEqual((open_push[2], open_push[3], close_push[3]), ("info", "deploy_drift", "deploy_drift_recovered"))
        self.assertIn("ticker/alerts.py", body)

    def test_all_deployed_closes_and_unknown_does_nothing(self):
        self.assertIs(self.run_it([{"surface": "a", "state": "DEPLOYED"}])[0][3], False)
        self.assertEqual(self.run_it([{"surface": "a", "state": "UNKNOWN"}]), [])

    def test_github_blob_lookup_and_file_reads_feed_the_check(self):
        class Response:
            status = 200

            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(self.body).encode()
        asked = []

        def opener(request, timeout=0):
            asked.append(request.full_url)
            return Response({"sha": drift.blob_sha(GOOD)})
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "f.py").write_bytes(GOOD)
            one = (drift.Surface("x", "lifeos/x.py", str(Path(tmp, "f.py")), "u.timer"),
                   drift.Surface("gone", "lifeos/y.py", str(Path(tmp, "gone.py")), "u.timer"))
            rows = drift.check(lambda p: ticker._status(ticker._request(f"repos/{ticker.REPO}/contents/{p}?ref=main", "t"), opener)[1]["sha"],
                               lambda p: ticker.Path(p).read_bytes() if ticker.Path(p).exists() else drift.MISSING, lambda u: "active", one)
        self.assertEqual([r["state"] for r in rows], ["DEPLOYED", "DRIFT"])
        self.assertTrue(all("?ref=main" in url for url in asked))


if __name__ == "__main__":
    unittest.main()
