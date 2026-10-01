"""One entry point for every pipeline stage:  python -m pipeline.run <stage> [--limit N] [--live]

A stage is a function (limit, live) -> counts. Add a stage by adding one line to STAGES; nothing else changes.
Errors from a stage are fixed codes only. Output is one counts-only line (public repo, D7).
"""
import argparse
import functools
import json
import sys

from pipeline import store


def _jobright(limit, live):
    from pipeline import jobright_browser, resolve_stage                                  # noqa: PLC0415
    return resolve_stage.run("jobright", limit, live, jobright_browser.resolve_many_sync)


def _lensa(limit, live):
    from pipeline import resolve_lensa, resolve_stage                                     # noqa: PLC0415
    return resolve_stage.run_rows("lensa", limit, live, resolve_lensa.make_resolver())


def _linkedin(limit, live):
    from pipeline import resolve_linkedin, resolve_stage                                  # noqa: PLC0415
    budget = {"left": resolve_linkedin.SEARCH_LIMIT}
    return resolve_stage.run_rows("linkedin-alerts", limit, live,
                                  functools.partial(resolve_linkedin.resolve_rows, budget=budget))


def _enrich(limit, live):
    from pipeline import enrich                                                           # noqa: PLC0415
    return enrich.run(limit, live)


def _audit(limit, live):
    from pipeline import audit                                                            # noqa: PLC0415
    return audit.run(max(limit, 2000), live)


def _publish(limit, live):
    from pipeline import notion                                                           # noqa: PLC0415
    return notion.run(limit, live)


def _sync_seen(limit, live):
    from pipeline import repost                                                           # noqa: PLC0415
    return repost.sync_last_seen(live=live)


def _purge(limit, live):
    from pipeline import purge                                                            # noqa: PLC0415
    return purge.run(live)


STAGES = {
    "resolve-jobright": _jobright,
    "resolve-linkedin": _linkedin,
    "resolve-lensa": _lensa,
    "enrich": _enrich,
    "audit": _audit,
    "publish": _publish,
    "sync-seen": _sync_seen,
    "purge": _purge,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=sorted(STAGES))
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--live", action="store_true", help="write results (default: dry run, counts only)")
    args = parser.parse_args(argv)
    from pipeline import notion                                                           # noqa: PLC0415
    try:
        counts = STAGES[args.stage](args.limit, args.live)
    except (store.StoreError, notion.NotionError) as error:
        print(f"{args.stage.upper()} FAILED: {error}", file=sys.stderr)
        return 1
    print(f"{args.stage}:", json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
