"""LIFE-518 pack helper: reads one tool output on stdin and prints ONLY a date, fixed method words or yes/no. Nothing else can leave this file.
  status   -> `date=YYYY-MM-DD|unknown` and `method=<fixed words>` (from `claude auth status`; the login expiry as a Central Time date)
  message  -> `ok=yes|no` (the one tiny message produced a non-error result)"""
import json
import re
import sys
from datetime import datetime, timezone

METHODS = {"claude.ai", "console", "api_key", "oauth_token", "third_party"}
PLANS = {"max", "pro", "team", "enterprise", "free"}


def as_date(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            when = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
        elif isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                return value
            when = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if when.tzinfo is None:
                return value[:10]
        else:
            return "unknown"
        try:
            from zoneinfo import ZoneInfo
            when = when.astimezone(ZoneInfo("America/Chicago"))
        except Exception:
            pass
        return when.strftime("%Y-%m-%d")
    except Exception:
        return "unknown"


def status(text):
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        raw = data.get("loginExpiresAt")
        auth, plan = str(data.get("authMethod", "")).lower(), str(data.get("subscriptionType", "")).lower()
    else:
        found = re.search(r"loginExpiresAt\W{1,4}([0-9T:.+\-Z]{10,40})", text)
        raw = found.group(1) if found else None
        low = text.lower()
        auth = next((m for m in sorted(METHODS) if m in low), "")
        plan = next((p for p in sorted(PLANS) if re.search(r"\b" + p + r"\b", low)), "")
    method = "/".join(w for w in (auth if auth in METHODS else "", plan if plan in PLANS else "") if w) or "unknown"
    print("date=" + as_date(raw))
    print("method=" + method)


def message(text):
    good = False
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            good = event.get("is_error") is False and event.get("subtype", "success") == "success"
    print("ok=" + ("yes" if good else "no"))


if __name__ == "__main__":
    (status if sys.argv[1:] == ["status"] else message)(sys.stdin.read())
