"""One retrying JSON call for the API clients (Jira, Google Calendar, Outlook). The caller builds a fresh Request for every attempt (so a
refreshed token is used); transient HTTP codes and network errors back off 2s, 4s, 8s; every failure becomes the client's own fixed code."""
import json
import urllib.error
import urllib.request

TRANSIENT = (429, 500, 502, 503, 504)


def _wait(error, attempt, honor_retry_after):
    if honor_retry_after and error.headers:
        try:
            return min(float(error.headers.get("Retry-After") or 0), 30) or 2 ** (attempt + 1)
        except (TypeError, ValueError):
            pass
    return 2 ** (attempt + 1)


def call(build, fail, prefix, sleep, timeout=30, attempts=4, bad_json="BAD_JSON", codes=None, honor_retry_after=False):
    """build() -> urllib Request. fail(code) -> the exception to raise. Returns the parsed JSON body ({} when empty)."""
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            with urllib.request.urlopen(build(), timeout=timeout) as response:
                raw = response.read()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            if error.code in TRANSIENT and not last:
                sleep(_wait(error, attempt, honor_retry_after))
                continue
            raise fail((codes or {}).get(error.code) or f"{prefix}_HTTP_{error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if not last:
                sleep(2 ** (attempt + 1))
                continue
            raise fail(f"{prefix}_NETWORK") from None
        except ValueError:
            raise fail(f"{prefix}_{bad_json}") from None
    raise fail(f"{prefix}_NETWORK")
