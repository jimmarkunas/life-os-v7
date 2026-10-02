"""Fetch a page the way a real Chrome does (TLS fingerprint and headers) for the few first-party sites that refuse plain scripted requests.
Used only for registry sources that set `impersonate`. Returns the same Fetched facts as lifeos.platform.http; URLs are never logged."""
import time

from lifeos.platform.http import Fetched

HEADERS = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
           "Accept-Language": "en-GB,en;q=0.9", "Cache-Control": "no-cache", "Pragma": "no-cache"}


def fetch(url, warm_url=None, alt_urls=(), must_contain="", timeout=25, rounds=3, max_bytes=3_000_000, session_factory=None, sleep=time.sleep):
    """Up to `rounds` tries over url + alt_urls; each try warms a fresh session on warm_url first. Succeeds on the first 200 whose body
    contains `must_contain`; otherwise returns the last status seen (0 = network or library missing)."""
    if session_factory is None:
        try:
            from curl_cffi import requests as browser                                        # noqa: PLC0415
        except ImportError:
            return Fetched(url, 0, "", 0, "no_impersonation")
        session_factory = lambda: browser.Session(impersonate="chrome", headers=HEADERS)     # noqa: E731
    last = Fetched(url, 0, "", 0, "network")
    for attempt in range(rounds):
        for candidate in (url, *alt_urls):
            try:
                session = session_factory()
                if warm_url:
                    session.get(warm_url, timeout=timeout, allow_redirects=True)
                response = session.get(candidate, timeout=timeout, allow_redirects=True)
                body = (response.text or "")[:max_bytes]
                last = Fetched(candidate, response.status_code, body if response.status_code == 200 else "", 0, "" if response.status_code == 200 else "http")
                if response.status_code == 200 and must_contain in body:
                    return last
            except Exception as error:                                                       # noqa: BLE001 - any transport error is a failed try
                last = Fetched(candidate, 0, "", 0, "timeout" if "timed out" in str(error).lower() else "network")
        if attempt < rounds - 1:
            sleep(0.75 * 2 ** attempt)
    return last
