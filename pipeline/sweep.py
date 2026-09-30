"""Stage 1 - sweep: move job-newsletter mail from Inbox to the J Newsletters label.

Idempotent by construction: moved mail leaves the Inbox, so the next run no longer sees it.
Usage: python -m pipeline.sweep [--live]   (default is a dry run that changes nothing)
"""
import argparse
import collections
import os
import sys

from pipeline import config
from pipeline.gmail import Gmail, GmailError


def sweep(gmail, live):
    label = gmail.label_id(config.NEWSLETTER_LABEL, create=live)
    by_rule = collections.defaultdict(list)
    skipped = 0
    for message_id in gmail.list_ids(config.GMAIL_QUERY):
        rule = config.classify(gmail.sender(message_id))
        if rule:
            by_rule[rule].append(message_id)
        else:
            skipped += 1
    moving = [i for ids in by_rule.values() for i in ids]
    if live and moving:
        gmail.relabel(moving, add=[label], remove=["INBOX"])
    return {"live": live, "moved": len(moving) if live else 0, "would_move": len(moving),
            "by_rule": {rule: len(ids) for rule, ids in sorted(by_rule.items())},
            "skipped_not_newsletter": skipped}


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
             f"moved={result['moved']} skipped={result['skipped_not_newsletter']} by_rule={result['by_rule']}"]
    print(lines[0])
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as handle:
            handle.write("### sweep\n" + lines[0] + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
