"""LIFE-761: one call closes a delivered ticket: Central-time stamp, the one receipt comment, the move to Done, and a read-back of both. Dry run unless live.

A batched PR passes every key; each ticket gets the same receipt listing every ticket and its proof. A ticket already
Done, or already carrying this PR's receipt, is not written again. Needs JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN.
Output: one line per ticket, key and outcome only."""
import argparse
import re
import sys

from lifeos.jira.release import text_of
from lifeos.platform.ct_stamp import stamp
from lifeos.platform.jira import Jira

DONE = "15"
REPO_PULL = "https://github.com/jimmarkunas/life-os-v7/pull/"
REPO_RUN = "https://github.com/jimmarkunas/life-os-v7/actions/runs/"
KEY = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


def receipt(keys, pr, sha, run, proofs, now=None):
    """-> the receipt text: an idempotency marker line (PR and short sha), then one line per ticket with its proof."""
    lines = [f"[receipt] {stamp(now)}: merged {REPO_PULL}{pr} at {sha[:8]}; same-head CI green {REPO_RUN}{run}."]
    for key in keys:
        lines.append(f"{key}: {proofs.get(key) or 'local proof as in the PR'}")
    return "\n".join(lines)


def marker(pr, sha):
    return f"merged {REPO_PULL}{pr} at {sha[:8]}"


def adf(text):
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in text.splitlines()]}


def read(jira, key):
    """-> (status name, [plain text of the newest comments]) from Jira, newest first."""
    issue = jira.get(f"/rest/api/3/issue/{key}", {"fields": "status"})
    status = ((issue.get("fields") or {}).get("status") or {}).get("name") or ""
    found = jira.get(f"/rest/api/3/issue/{key}/comment", {"orderBy": "-created", "maxResults": 20})
    return status, [text_of(c.get("body")) for c in (found.get("comments") or [])]


def close(jira, key, text, mark, live=False):
    """-> {"key", "outcome", "problems"}; outcomes: dry_run, closed (comment and Done read back), already_done, partial."""
    status, comments = read(jira, key)
    done = status.lower() == "done"
    has = any(mark in c for c in comments)
    if done and has:
        return {"key": key, "outcome": "already_done", "problems": []}
    if not live:
        return {"key": key, "outcome": "dry_run", "problems": [] + ([] if has else ["would comment"]) + ([] if done else ["would move to Done"])}
    if not has:
        jira.post(f"/rest/api/3/issue/{key}/comment", {"body": adf(text)})
    if not done:
        jira.post(f"/rest/api/3/issue/{key}/transitions", {"transition": {"id": DONE}})
    status, comments = read(jira, key)
    problems = ([] if status.lower() == "done" else [f"status reads {status or 'empty'}"]) + ([] if any(mark in c for c in comments) else ["receipt comment not read back"])
    return {"key": key, "outcome": "partial" if problems else "closed", "problems": problems}


def main(argv=None, jira=None, now=None, out=sys.stdout):
    ap = argparse.ArgumentParser(prog="python -m lifeos.jira.closeout", description=__doc__.splitlines()[0])
    ap.add_argument("keys", nargs="+")
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--run", required=True, type=int)
    ap.add_argument("--proof", action="append", default=[], help="KEY=text, once per ticket")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args(argv)
    bad = [k for k in args.keys if not KEY.match(k)] + ([] if re.fullmatch(r"[0-9a-f]{40}", args.sha) else ["sha"])
    if bad:
        print("closeout: bad argument: " + ", ".join(bad), file=out)
        return 2
    proofs = dict(p.split("=", 1) for p in args.proof if "=" in p)
    jira = jira or Jira.from_env()
    text, mark = receipt(args.keys, args.pr, args.sha, args.run, proofs, now), marker(args.pr, args.sha)
    worst = 0
    for key in args.keys:
        result = close(jira, key, text, mark, args.live)
        print(f"closeout: {key} {result['outcome']}" + (": " + "; ".join(result["problems"]) if result["problems"] else ""), file=out)
        worst = worst or int(result["outcome"] == "partial")
    return worst


if __name__ == "__main__":
    sys.exit(main())
