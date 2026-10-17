"""D202: the employer-board pass. About 10,700 Greenhouse, Ashby and Lever boards (the Open Jobs board list, CC0, plus the extras in
boards.json) read through the same web run as the priority boards: the same states, diff, FAILED-is-never-a-removal rule and admission.
Differences, all bounded: more boards per run on fewer workers, a slower refresh unless the board lists a target job, a smaller admission
budget, only clear targets stored (`keep` runs before the diff), and an ATS that answers 429 three times is not read
again this run (its boards are FAILED `skipped_after_rate_limit_429` and retried later)."""
import re
import threading

from lifeos.jobs import lanes
from lifeos.platform import limits
from lifeos.sources.web import lister, registry, run as web, suppress

LANE = "US Remote"
# A seed board is an unknown employer, so it must show the target, not merely fail to rule it out (the priority boards keep the looser test):
FAMILY = re.compile(r"\b(program(?:me)? manager|project manager|technical program|tpm|delivery (?:lead|manager|director)|program director|"
                    r"implementation (?:manager|lead|consultant)|solutions? (?:architect|consultant)|engagement manager|pmo)\b", re.I)
US = re.compile(r"united states|\busa?\b|\bu\.s\.", re.I)
REMOTE = re.compile(r"\bremote\b|\banywhere\b", re.I)
PLACELESS = re.compile(r"^\W*(?:fully\W+)?(?:remote|anywhere|worldwide|global)(?:\W+(?:remote|anywhere|worldwide|global))*\W*$", re.I)   # "Remote", "Remote - Anywhere"
WHOLE_US = re.compile(r"^\W*(?:united states(?: of america)?|usa?|u\.s\.(?:a\.)?)\W*$", re.I)                                        # the country, no city


def keep(job):
    """A US Remote target worth storing: a target-family title, and a place that is remote in the US, plain remote, or the whole US with no
    city ("Remote - Colombia" and "Austin, Texas, United States" are not); then no miss the web pass's own cheap suppression would drop (first
    party, so age is no reason). Everything else on the board is never stored."""
    title, where = job["title"], job["location"] or ""
    if not FAMILY.search(title):
        return False
    if not ((REMOTE.search(where) and (US.search(where) or PLACELESS.match(where))) or WHOLE_US.match(where)):
        return False
    if lanes.detect_work_mode(where, title) in ("onsite", "hybrid"):
        return False
    return suppress.reason(job, None, LANE, first_party=True) is None


class StopOnRateLimit:
    """Wraps a lister: after `limit` rate_limited_429 answers from one ATS family, its remaining boards are not requested this run."""

    def __init__(self, lister_fn, limit=None):
        self.lister_fn, self.limit, self.hits, self.lock = lister_fn, limit or limits.BOARDS_RATE_LIMIT_STOP, {}, threading.Lock()

    def __call__(self, source):
        kind = source["kind"]
        with self.lock:
            if self.hits.get(kind, 0) >= self.limit:
                return lister.Listing(lister.FAILED, reason="skipped_after_rate_limit_429")
        listing = self.lister_fn(source)
        if listing.reason == "rate_limited_429":
            with self.lock:
                self.hits[kind] = self.hits.get(kind, 0) + 1
        return listing


def run(limit, live, now=None, lister_fn=lister.list_source, repo=None, sources=None):
    counts = web.run(limit, live, now, StopOnRateLimit(lister_fn), repo, registry.boards() if sources is None else sources, LANE,
                     cap=limits.BOARDS_PER_RUN, budget=limits.BOARDS_INGEST_PER_RUN, keep=keep, workers=limits.BOARDS_WORKERS)
    counts["pass"] = "employer boards"
    return counts
