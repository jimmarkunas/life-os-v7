"""Polite HTTP fetch with manual redirect following. Returns facts only; URLs are never logged (public repo)."""
from collections.abc import Iterable
from typing import Any
import http.client
import urllib.error
import urllib.request
from urllib.parse import urljoin

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
REDIRECTS = (301, 302, 303, 307, 308)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class Fetched:
    __slots__ = ("final_url", "status", "html", "hops", "error", "codes", "validators")

    def __init__(self, final_url: str, status: int, html: str = "", hops: int = 0, error: str = "", codes: Iterable[int] = (), validators: dict[str, str] | None = None) -> None:
        self.final_url, self.status, self.html, self.hops, self.error = final_url, status, html, hops, error
        self.codes = tuple(codes)      # status code of every hop, e.g. (302, 403)
        self.validators = validators or {}     # D216: the answer's ETag / Last-Modified, for a later conditional GET


def _validators(headers):
    """D216: the cache validators an answer carries ({"etag": ..., "last_modified": ...}), each only when present and of a sane length."""
    if not headers:
        return {}
    out = {"etag": headers.get("ETag"), "last_modified": headers.get("Last-Modified")}
    return {k: v.strip() for k, v in out.items() if isinstance(v, str) and 0 < len(v.strip()) <= 200}


def fetch(url: str, timeout: int = 12, max_hops: int = 8, max_bytes: int = 1_500_000, headers: dict[str, str] | None = None, data: bytes | None = None) -> Fetched:
    current, codes = url, []
    for hop in range(max_hops + 1):
        request = urllib.request.Request(current, data=data if hop == 0 else None, headers={
            "User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9", **(headers or {})})
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                body = response.read(max_bytes).decode(response.headers.get_content_charset() or "utf-8", "replace")
                codes.append(response.status)
                return Fetched(current, response.status, body, hop, codes=codes, validators=_validators(response.headers))
        except urllib.error.HTTPError as error:
            codes.append(error.code)
            location = error.headers.get("Location") if error.headers else None
            if error.code in REDIRECTS and location:
                current = urljoin(current, location)
                continue
            return Fetched(current, error.code, "", hop, "http", codes, _validators(error.headers) if error.code == 304 else None)
        except (http.client.InvalidURL, ValueError):                 # a redirect to a malformed address (a bot-protection page): a miss, never a crash
            return Fetched(current, 0, "", hop, "bad_url", codes)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return Fetched(current, 0, "", hop, "timeout" if "timed out" in str(error).lower() else "network", codes)
    return Fetched(current, 0, "", max_hops, "too_many_redirects", codes)
