"""Stage 1 - sweep: move job-newsletter mail from Inbox to the J Newsletters label.

Idempotent by construction: moved mail leaves the Inbox, so the next run no longer sees it.
Usage: python -m lifeos.sources.newsletters.sweep [--live]   (default is a dry run that changes nothing)
"""
import argparse
import os
import sys

from lifeos.sources.newsletters import config
from lifeos.platform.gmail import Gmail, GmailError


def sweep(gmail, live):
    label = gmail.label_id(config.NEWSLETTER_LABEL, create=live)
    by_rule, seen = {}, set()
    for rule, query in config.GMAIL_QUERIES.items():
        ids = [i for i in gmail.list_ids(query) if i not in seen]
        seen.update(ids)
        by_rule[rule] = len(ids)
    moving = sorted(seen)
    if live and moving:
        gmail.relabel(moving, add=[label], remove=["INBOX"])
    return {"live": live, "moved": len(moving) if live else 0, "would_move": len(moving), "by_rule": by_rule}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="actually relabel mail")
    args = parser.parse_args(argv)
    try:
        result = sweep(Gmail.from_env(), args.live)
    except GmailError as error:
        print(f"SWEEP FAILED: {error}", file=sys.stderr)
        return 1
    lines = [f"sweep {'LIVE' if result['live'] else 'DRY-RUN'}: would_move={result['would_move']} "
             f"moved={result['moved']} by_rule={result['by_rule']}"]
    print(lines[0])
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as handle:
            handle.write("### sweep\n" + lines[0] + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
