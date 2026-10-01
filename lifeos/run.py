"""One entry point for every pipeline stage:  python -m lifeos.run <stage> [--limit N] [--live]

A stage is a function (limit, live) -> counts. Add a stage by adding one line to STAGES; nothing else changes.
Errors from a stage are fixed codes only. Output is one counts-only line (public repo, D7).
"""
import argparse
import functools
import json
import sys

from lifeos.platform.db import StoreError
from lifeos.platform.notion_client import NotionError


def _jobright(limit, live):
    from lifeos.jobs.resolve.aggregators import jobright                                  # noqa: PLC0415
    from lifeos.jobs.resolve import stage                                  # noqa: PLC0415
    return stage.run("jobright", limit, live, jobright.resolve_many_sync)


def _lensa(limit, live):
    from lifeos.jobs.resolve.aggregators import lensa                                     # noqa: PLC0415
    from lifeos.jobs.resolve import stage                                     # noqa: PLC0415
    return stage.run_rows("lensa", limit, live, lensa.make_resolver())


def _linkedin(limit, live):
    from lifeos.jobs.resolve.aggregators import linkedin                                  # noqa: PLC0415
    from lifeos.jobs.resolve import stage                                  # noqa: PLC0415
    budget = {"left": linkedin.SEARCH_LIMIT}
    return stage.run_rows("linkedin-alerts", limit, live,
                                  functools.partial(linkedin.resolve_rows, budget=budget))


def _web(limit, live):
    from lifeos.sources.web import run as web                                                   # noqa: PLC0415
    return web.run(limit, live)


def _web_scale_up(limit, live):
    from lifeos.sources.web import run as web                                                   # noqa: PLC0415
    return web.run(limit, live, lane="Scale-Up")


def _enrich(limit, live):
    from lifeos.jobs import enrich                                                           # noqa: PLC0415
    return enrich.run(limit, live)


def _fit(limit, live):
    from lifeos.jobs.fit import stage                                                          # noqa: PLC0415
    return stage.run(max(limit, 500), live)


def _audit(limit, live):
    from lifeos.jobs import audit                                                            # noqa: PLC0415
    return audit.run(max(limit, 2000), live)


def _publish(limit, live):
    from lifeos.jobs import publish                                                           # noqa: PLC0415
    return publish.run(limit, live)


def _sync_seen(limit, live):
    from lifeos.jobs import repost                                                           # noqa: PLC0415
    return repost.sync_last_seen(live=live)


def _purge(limit, live):
    from lifeos.jobs import retention                                                            # noqa: PLC0415
    return retention.run(live)


STAGES = {
    "resolve-jobright": _jobright,
    "resolve-linkedin": _linkedin,
    "resolve-lensa": _lensa,
    "web": _web,
    "web-scale-up": _web_scale_up,
    "enrich": _enrich,
    "fit": _fit,
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
    try:
        counts = STAGES[args.stage](args.limit, args.live)
    except (StoreError, NotionError) as error:
        print(f"{args.stage.upper()} FAILED: {error}", file=sys.stderr)
        return 1
    print(f"{args.stage}:", json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
