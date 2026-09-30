"""Polite HTTP fetch with manual redirect following. Returns facts only; URLs are never logged (public repo)."""
import urllib.error
import urllib.request
from urllib.parse import urljoin

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
REDIRECTS = (301, 302, 303, 307, 308)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class Fetched:
    __slots__ = ("final_url", "status", "html", "hops", "error", "codes")

    def __init__(self, final_url, status, html="", hops=0, error="", codes=()):
        self.final_url, self.status, self.html, self.hops, self.error = final_url, status, html, hops, error
        self.codes = tuple(codes)      # status code of every hop, e.g. (302, 403)


def fetch(url, timeout=12, max_hops=8, max_bytes=1_500_000):
    current, codes = url, []
    for hop in range(max_hops + 1):
        request = urllib.request.Request(current, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
                                                           "Accept-Language": "en-US,en;q=0.9"})
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                body = response.read(max_bytes).decode(response.headers.get_content_charset() or "utf-8", "replace")
                codes.append(response.status)
                return Fetched(current, response.status, body, hop, codes=codes)
        except urllib.error.HTTPError as error:
            codes.append(error.code)
            location = error.headers.get("Location") if error.headers else None
            if error.code in REDIRECTS and location:
                current = urljoin(current, location)
                continue
            return Fetched(current, error.code, "", hop, "http", codes)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return Fetched(current, 0, "", hop, "timeout" if "timed out" in str(error).lower() else "network", codes)
    return Fetched(current, 0, "", max_hops, "too_many_redirects", codes)
