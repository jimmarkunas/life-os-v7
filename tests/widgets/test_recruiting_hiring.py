"""LIFE-47 UI-only slice: the Recruiting & Hiring widget is synthetic and can reach nothing. It is served from a public host, so the guard is structural:
no network, storage, navigation or external URL in the page, and the approved V2 reference is kept byte-for-byte."""
import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WIDGET = (ROOT / "widgets/recruiting-hiring/index.html").read_text(encoding="utf-8")
APPROVED = ROOT / "docs/prototypes/recruiting_hiring_widget_v2.html"
APPROVED_SHA256 = "f6877cab368ca25686ef"


class RecruitingHiringWidgetTests(unittest.TestCase):
    def test_the_page_can_reach_nothing(self):
        self.assertIn("default-src 'none'", WIDGET)                                   # CSP: no fetch, XHR, image, frame, font or form target
        self.assertIsNone(re.search(r"https?://|//[a-z0-9.-]+\.[a-z]{2,}", WIDGET, re.I))
        for api in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "localStorage", "sessionStorage", "indexedDB", "document.cookie", "window.open", "location.href", "location.assign", "<iframe", "<img", "<script src", "<link"):
            self.assertNotIn(api, WIDGET)

    def test_it_says_it_is_synthetic_and_keeps_the_approved_shape(self):
        self.assertIn("synthetic preview", WIDGET)
        self.assertIn("No real data saved", WIDGET)
        self.assertNotRegex(WIDGET, r"type=[\"']search|placeholder=|eyebrow")            # approved: no search bar, no eyebrow
        for state in ("normal", "empty", "degraded"):
            self.assertIn(f'data-state="{state}"', WIDGET)

    def test_the_approved_v2_reference_is_kept_unchanged(self):
        self.assertTrue(hashlib.sha256(APPROVED.read_bytes()).hexdigest().startswith(APPROVED_SHA256))


if __name__ == "__main__":
    unittest.main()
