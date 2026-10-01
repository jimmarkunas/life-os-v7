"""Lensa rows -> final apply link. Lensa pages are blocked from the runner, so nothing here touches Lensa: the card's
company / title / location are matched against the employer's public ATS boards, then the free Search API."""
from lifeos.platform import limits, tinyfish_search
from lifeos.jobs.resolve.aggregators import linkedin


def make_resolver():
    """One search budget for the whole run, shared by every batch the stage hands in."""
    budget = {"left": limits.TINYFISH_SEARCH_PER_RUN_LENSA}
    tinyfish_search.solo_after(limits.TINYFISH_SEARCH_SOLO_AFTER_SECONDS)       # LinkedIn's lane is finished by then: use the full 27/min

    def resolve_rows(rows):
        return linkedin.match_rows(rows, [{"outcome": "external_hidden"} for _ in rows], budget=budget)
    return resolve_rows


resolve_rows = make_resolver()
