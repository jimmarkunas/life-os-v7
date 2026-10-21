"""LIFE-88 QA probe: manual only, typed QA confirmation, and nothing but the QA render proof (tested in test_ui3c.QaAcceptanceRun)."""
import unittest
import unittest.mock
from lifeos.interview import ui_probe as P
from lifeos.platform.notion_client import NotionError

LIVE = {"PROBE_CONFIRM": "QA", "NOTION_INTERVIEW_TOKEN": "synthetic"}


class ProbeTests(unittest.TestCase):
    def test_refuses_without_live_and_typed_qa(self):
        for live, env in ((False, LIVE), (True, {**LIVE, "PROBE_CONFIRM": ""}), (True, {**LIVE, "PROBE_CONFIRM": "SYNTHETIC"})):
            with self.subTest(live=live, env=env), self.assertRaises(NotionError):
                P.run(0, live, environ=env, client=object())

    def test_the_probe_is_the_qa_render_proof_only(self):
        with unittest.mock.patch.object(P, "_render", return_value={"qa": "stubbed"}) as render:
            counts = P.run(0, True, environ=LIVE, client=object())
        render.assert_called_once()
        self.assertEqual(counts, {"interview_ui_probe": "live", "checks": {"qa_render": "ok"}, "failed": 0, "qa_render": {"qa": "stubbed"}})
        with unittest.mock.patch.object(P, "_render", side_effect=NotionError("NOTION_HTTP_500")):
            failed = P.run(0, True, environ=LIVE, client=object())
        self.assertEqual((failed["checks"], failed["failed"]), ({"qa_render": "NOTION_HTTP_500"}, 1))


if __name__ == "__main__":
    unittest.main()
