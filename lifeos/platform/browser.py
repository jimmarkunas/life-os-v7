"""Fetch a page with a real headless Chromium (free, Playwright) for the few first-party careers sites that refuse every plain or Chrome-fingerprint request.
Used only for registry sources that set `browser`. Returns lifeos.platform.http.Fetched facts; URLs are never logged. Playwright is imported lazily:
without it (or without the Chromium binary) the result is status 0 / error `no_browser`, which a lister turns into FAILED, never zero jobs."""
from lifeos.platform.http import Fetched

LAUNCH_ARGS = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def fetch(url, warm_url=None, wait_for=None, settle_ms=2500, timeout_ms=30000, max_bytes=3_000_000, playwright_factory=None):
    """Load `url` (after an optional warm-up page), wait for `wait_for` (a CSS selector) or `settle_ms`, return the rendered HTML."""
    if playwright_factory is None:
        try:
            from playwright.sync_api import sync_playwright                                   # noqa: PLC0415
        except ImportError:
            return Fetched(url, 0, "", 0, "no_browser")
        playwright_factory = sync_playwright
    try:
        with playwright_factory() as pw:
            browser = pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
            try:
                context = browser.new_context(user_agent=USER_AGENT, locale="en-GB", viewport={"width": 1366, "height": 900})
                page = context.new_page()
                if warm_url:
                    page.goto(warm_url, wait_until="domcontentloaded", timeout=timeout_ms)
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if wait_for:
                    try:
                        page.wait_for_selector(wait_for, timeout=timeout_ms)
                    except Exception:                                                         # noqa: BLE001 - the page simply never showed it
                        pass
                else:
                    page.wait_for_timeout(settle_ms)
                status = response.status if response else 0
                html = page.content()[:max_bytes]
                return Fetched(page.url, status, html if status == 200 else "", 0, "" if status == 200 else "http")
            finally:
                browser.close()
    except Exception as error:                                                                # noqa: BLE001 - any browser failure is a failed fetch
        return Fetched(url, 0, "", 0, "timeout" if "timeout" in str(error).lower() else "no_browser")
