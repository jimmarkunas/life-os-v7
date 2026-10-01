"""Lensa rows -> final apply link. Lensa pages are blocked from the runner, so nothing here touches Lensa: the card's
company / title / location are matched against the employer's public ATS boards, then the free Search API."""
from pipeline import limits, resolve_linkedin


def resolve_rows(rows):
    return resolve_linkedin.match_rows(rows, [{"outcome": "external_hidden"} for _ in rows],
                                       limits.TINYFISH_SEARCH_PER_RUN_LENSA)
