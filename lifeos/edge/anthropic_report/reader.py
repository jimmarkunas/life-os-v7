#!/usr/bin/python3 -I
"""LIFE-921: billed Anthropic spend, read from Anthropic's own usage and cost report with the Admin key, for the Daily Report's Anthropic rows.

One socket-activated unit (lifeos-anthropic-report@.service) runs this once per connection from Oracle's stats script and writes ONE JSON line: {"billed_usd", "honcho_usd", "as_of"}; any number it cannot have is null (never zero)
and nothing else is ever written (no key, no id, no error text). The Admin key arrives only as the unit's own encrypted credential (CREDENTIALS_DIRECTORY/anthropic_admin_key), readable by no other unit or user.
An Admin key can manage the organization, so this program may call ONLY the two report endpoints below, on api.anthropic.com, with GET: `get` refuses any other path, host or method (tests pin it).
billed_usd is every cost the report shows from the start of the current credit window on (the 5th at 6:00 PM CT, LIFE-950; it resets with the credit) (the report's `amount` is US cents, a decimal string; Nov 21 2026: 2.27 cents = the $0.02 the LIFE-889 proof was charged). honcho_usd is the same figure when the usage report
shows exactly one API key in use over the same days (Honcho's own, the only key), else null: a second key would make the attribution a guess, and a guess is shown as UNVERIFIED. Stdlib only."""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HOST = "api.anthropic.com"
VERSION = "2023-06-01"
CENTRAL = ZoneInfo("America/Chicago")


def since_now(now=None):
    """-> the start of the current credit window as the report API's UTC timestamp: the 5th at 6:00 PM CT (LIFE-950; the same rule as lifeos/platform/credit_window.py, pinned by a test)."""
    local = (now or datetime.now(timezone.utc)).astimezone(CENTRAL)
    year, month = local.year, local.month
    if local < datetime(year, month, 5, 18, tzinfo=CENTRAL):
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return datetime(year, month, 5, 18, tzinfo=CENTRAL).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


ALLOWED = ("/v1/organizations/cost_report", "/v1/organizations/usage_report/messages")
MAX_PAGES = 12
TIMEOUT = 25


class Refused(Exception):
    pass


def get(path, params, key, opener=urllib.request.urlopen):
    """-> the decoded JSON of one GET on an allowed report path; anything else raises Refused before a request is made."""
    if path not in ALLOWED:
        raise Refused("path")
    query = urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(f"https://{HOST}{path}?{query}", headers={"x-api-key": key, "anthropic-version": VERSION}, method="GET")
    with opener(request, timeout=TIMEOUT) as reply:
        return json.loads(reply.read())


def pages(path, params, key, opener):
    """Every page of one report (the report says has_more and next_page); bounded."""
    cursor = None
    for _ in range(MAX_PAGES):
        body = get(path, {**params, **({"page": cursor} if cursor else {})}, key, opener)
        yield from body.get("data", [])
        if not body.get("has_more") or not body.get("next_page"):
            return
        cursor = body["next_page"]
    raise ValueError("too many pages")


def billed_usd(key, opener, since):
    """Total cost since `since`, in US dollars (the report's amounts are cents)."""
    cents = 0.0
    for bucket in pages(ALLOWED[0], {"starting_at": since, "bucket_width": "1d", "limit": 31}, key, opener):
        for result in bucket.get("results", []):
            if result.get("currency", "USD") != "USD":
                raise ValueError("currency")
            cents += float(result["amount"])
    return round(cents / 100, 4)


def one_key(key, opener, since):
    """True when the usage report shows at most one API key with usage since `since`."""
    keys = set()
    for bucket in pages(ALLOWED[1], {"starting_at": since, "bucket_width": "1d", "limit": 31, "group_by[]": "api_key_id"}, key, opener):
        for result in bucket.get("results", []):
            keys.add(result.get("api_key_id"))
    return len(keys) <= 1


def reading(key, opener=urllib.request.urlopen, now=None):
    """-> {"billed_usd", "honcho_usd", "as_of"}; nulls for what could not be had."""
    moment = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    since = since_now(now)
    try:
        billed = billed_usd(key, opener, since)
    except Exception:                                                  # noqa: BLE001 - every failure is a null, never text
        return {"billed_usd": None, "honcho_usd": None, "as_of": None}
    try:
        attributed = billed if one_key(key, opener, since) else None
    except Exception:                                                  # noqa: BLE001
        attributed = None
    return {"billed_usd": billed, "honcho_usd": attributed, "as_of": moment}


def main():
    try:
        with open(os.path.join(os.environ["CREDENTIALS_DIRECTORY"], "anthropic_admin_key")) as handle:
            key = handle.read().strip()
    except (KeyError, OSError):
        key = ""
    out = reading(key) if key else {"billed_usd": None, "honcho_usd": None, "as_of": None}
    sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
