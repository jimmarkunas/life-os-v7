"""LIFE-1117: delete old completed workflow runs of this repository so the run list stays at about 30 days.

    python tools/run_cleanup.py            dry run (the default): inventory, validate, delete nothing
    python tools/run_cleanup.py --live     delete (--dry-run beside it keeps the dry run)

Reads GITHUB_TOKEN and GITHUB_REPOSITORY from the environment. The token is the workflow's own, with job-scoped
`actions: write` and nothing else. Rules (pre-made in the ticket):
  - a candidate is a COMPLETED run created strictly more than 30 days before this run starts; queued and in-progress
    runs, runs newer than the cutoff and the IDs in .github/protected-runs are never candidates;
  - the whole candidate set is inventoried and validated first, then deleted oldest first, one request at a time, at most
    one per second, no retry and no wait;
  - at most 600 calls per run (inventory included); stop when x-ratelimit-remaining is below 300; stop at once on a 403,
    a 429 or a retry-after header;
  - every run starts from a fresh inventory, so a stop leaves the rest for the next run.
The inventory uses the `created` filter, which GitHub caps at 1,000 results (newest first); a larger backlog is flagged
`truncated` and the next run takes the rest. Output is counts, one run ID and Central times only. Exit 1 on a 403, 429,
retry-after or other HTTP or network failure; a cap, a low rate limit or a finished list exit 0."""

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lifeos.platform import ct_stamp  # noqa: E402

API = "https://api.github.com"
PROTECTED_FILE = ROOT / ".github" / "protected-runs"
KEEP_DAYS = 30
MAX_CALLS = 600
MIN_REMAINING = 300
PAGE = 100
MAX_PAGES = 10  # GitHub returns at most 1,000 results for a filtered search
PAUSE = 1.0  # seconds between deletes
OK_STOPS = {"done", "dry_run", "call_cap", "rate_low"}


class Stop(Exception):
    """A fixed stop code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def load_protected(path=None):
    ids = set()
    for line in (path or PROTECTED_FILE).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line.isdigit():
            ids.add(int(line))
    return ids


def cutoff_for(now):
    return now - timedelta(days=KEEP_DAYS)


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class Client:
    """One GitHub call at a time, counting calls and remembering the rate-limit headers."""

    def __init__(self, token, repo, opener=urllib.request.urlopen):
        self.token, self.repo, self.opener = token, repo, opener
        self.calls, self.remaining, self.reset = 0, None, None

    def call(self, method, path):
        """-> (status, parsed JSON or None). Raises Stop on 403, 429, retry-after, other HTTP errors or a network failure."""
        req = urllib.request.Request(
            f"{API}/repos/{self.repo}{path}",
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"},
        )
        self.calls += 1
        try:
            resp = self.opener(req, timeout=30)
            status, headers, body = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as err:
            status, headers, body = err.code, err.headers, b""
        except (urllib.error.URLError, OSError):
            raise Stop("network_error") from None
        self._note(headers)
        if headers.get("retry-after") is not None:
            raise Stop("retry_after")
        if status == 403:
            raise Stop("forbidden_403")
        if status == 429:
            raise Stop("rate_limited_429")
        if status >= 400 and not (method == "DELETE" and status == 404):
            raise Stop(f"http_{status}")
        return status, (json.loads(body) if body else None)

    def _note(self, headers):
        left, reset = headers.get("x-ratelimit-remaining"), headers.get("x-ratelimit-reset")
        if left is not None and str(left).isdigit():
            self.remaining = int(left)
        if reset is not None and str(reset).isdigit():
            self.reset = int(reset)


def inventory(client, cutoff):
    """-> (runs created before the cutoff, truncated). Raises Stop(rate_low) when the limit falls below the floor mid-listing."""
    stamp = cutoff.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs, total = [], 0
    for page in range(1, MAX_PAGES + 1):
        _, data = client.call("GET", f"/actions/runs?per_page={PAGE}&page={page}&created=%3C{stamp}")
        batch = (data or {}).get("workflow_runs", [])
        total = (data or {}).get("total_count", 0)
        runs.extend(batch)
        if len(batch) < PAGE:
            break
        if client.remaining is not None and client.remaining < MIN_REMAINING:
            raise Stop("rate_low")
    return runs, total > len(runs)


def validate(runs, cutoff, protected):
    """-> (candidates oldest first, counts of what was left out). Every candidate is completed, older than the cutoff and unprotected."""
    seen, keep = set(), []
    left = {"not_completed": 0, "protected": 0, "newer": 0}
    for run in runs:
        rid = int(run["id"])
        if rid in seen:
            continue
        seen.add(rid)
        if rid in protected:
            left["protected"] += 1
        elif run.get("status") != "completed":
            left["not_completed"] += 1
        elif not parse_time(run["created_at"]) < cutoff:
            left["newer"] += 1
        else:
            keep.append(run)
    keep.sort(key=lambda r: (parse_time(r["created_at"]), int(r["id"])))
    return keep, left


def run(client, now, live, protected, sleep=time.sleep):
    """One cleanup run -> the receipt (a dict). Never raises: a stop is the receipt's `stop_reason`."""
    cutoff = cutoff_for(now)
    receipt = {
        "mode": "live" if live else "dry_run",
        "cutoff": ct_stamp.stamp(cutoff),
        "inventoried": 0,
        "candidates": 0,
        "left_out": {},
        "truncated": False,
        "deleted": 0,
        "already_gone": 0,
        "last_run_id": None,
        "stop_reason": "done",
    }
    try:
        runs, receipt["truncated"] = inventory(client, cutoff)
        receipt["inventoried"] = len(runs)
        candidates, receipt["left_out"] = validate(runs, cutoff, protected)
        receipt["candidates"] = len(candidates)
        if not live:
            receipt["stop_reason"] = "dry_run"
        else:
            for item in candidates:
                if client.calls >= MAX_CALLS:
                    raise Stop("call_cap")
                if client.remaining is not None and client.remaining < MIN_REMAINING:
                    raise Stop("rate_low")
                if receipt["deleted"] + receipt["already_gone"]:
                    sleep(PAUSE)
                status, _ = client.call("DELETE", f"/actions/runs/{int(item['id'])}")
                receipt["last_run_id"] = int(item["id"])
                receipt["already_gone" if status == 404 else "deleted"] += 1
    except Stop as stop:
        receipt["stop_reason"] = stop.code
    receipt["calls"] = client.calls
    receipt["rate_remaining"] = client.remaining
    receipt["rate_reset"] = (
        ct_stamp.stamp(datetime.fromtimestamp(client.reset, tz=timezone.utc)) if client.reset else None
    )
    return receipt


def summary(receipt):
    left = receipt["left_out"]
    lines = [
        f"Run cleanup receipt ({receipt['mode']})",
        f"- Cutoff: runs created before {receipt['cutoff']}",
        f"- Candidates: {receipt['candidates']} of {receipt['inventoried']} inventoried"
        + (" (inventory truncated at 1,000; the next run takes the rest)" if receipt["truncated"] else ""),
        f"- Left out of the candidates: not completed {left.get('not_completed', 0)}, protected {left.get('protected', 0)}, newer {left.get('newer', 0)}",
        f"- Deleted: {receipt['deleted']} (already gone {receipt['already_gone']}); last run ID: {receipt['last_run_id']}",
        f"- Calls: {receipt['calls']}; rate remaining: {receipt['rate_remaining']}; resets {receipt['rate_reset']}",
        f"- Stop reason: {receipt['stop_reason']}",
    ]
    return "\n".join(lines)


def main(argv=None, env=None, opener=urllib.request.urlopen, now=None, sleep=time.sleep, out=sys.stdout):
    env = os.environ if env is None else env
    argv = sys.argv[1:] if argv is None else argv
    token, repo = env.get("GITHUB_TOKEN", ""), env.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("RUN_CLEANUP_NO_TOKEN_OR_REPO", file=out)
        return 2
    live = "--live" in argv and "--dry-run" not in argv  # an explicit --dry-run always wins
    receipt = run(Client(token, repo, opener), now or datetime.now(timezone.utc), live, load_protected(), sleep)
    text = summary(receipt)
    print(text, file=out)
    print(json.dumps(receipt, sort_keys=True), file=out)
    path = env.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as handle:
            handle.write(text + "\n")
    return 0 if receipt["stop_reason"] in OK_STOPS else 1


if __name__ == "__main__":
    sys.exit(main())
