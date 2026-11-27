"""Which tickets sat 60 minutes or more with nothing on the record, read-only, from a saved Jira export.

  python -m lifeos.sources.devtools.record_gaps FILE [--minutes 60] [--now ISO]

FILE is {"LIFE-nnn": [[ISO time, text], ...]}: every comment and status move of a ticket. A stretch runs from one
entry to the next (the last to --now) and is EXPLAINED when the entry that opens it starts with `[waiting on]`,
else UNEXPLAINED. Prints keys, CT times and minutes only.
"""
import argparse
import datetime as dt
import json
import sys

from lifeos.platform.ct_stamp import stamp
from lifeos.sources.devtools.profile_tickets import when


def stretches(entries, now, limit=60):
    ordered = sorted((when(time), text) for time, text in entries)
    ends = [time for time, _ in ordered[1:]] + [now]
    return [(start, round((end - start).total_seconds() / 60), text.startswith("[waiting on]")) for (start, text), end in zip(ordered, ends) if end - start >= dt.timedelta(minutes=limit)]


def main(argv=None, out=sys.stdout):
    parser = argparse.ArgumentParser(description="Read-only: stretches with nothing on a ticket's record.")
    parser.add_argument("file")
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--now", help="ISO time to count the last stretch to (default: the clock)")
    args = parser.parse_args(argv)
    now = when(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    with open(args.file) as handle:
        tickets = json.load(handle)
    open_count = 0
    for key in sorted(tickets):
        found = stretches(tickets[key], now, args.minutes)
        if not tickets[key]:
            out.write(f"{key} NO_ENTRIES\n")
        for start, minutes, explained in found:
            open_count += not explained
            out.write(f"{key} {stamp(start)} {minutes} min {'EXPLAINED' if explained else 'UNEXPLAINED'}\n")
    out.write(f"unexplained stretches: {open_count}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
