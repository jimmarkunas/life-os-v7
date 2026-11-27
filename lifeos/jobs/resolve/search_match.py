"""Find the employer's own posting through web search (TinyFish Search), precision first.

Accept only when the result title contains the job title AND the company name appears in host/title/snippet.
"""
from lifeos.jobs.resolve import ats_match
from lifeos.jobs import classify, quality
from lifeos.platform import tinyfish_search
from lifeos.platform.tinyfish import TinyFishError

ATS_DOMAINS = ("greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "icims.com", "smartrecruiters.com",
               "workable.com", "jobvite.com", "bamboohr.com", "recruitee.com", "applytojob.com", "teamtailor.com",
               "taleo.net", "successfactors.com", "oraclecloud.com", "paylocity.com", "ultipro.com", "dayforcehcm.com")


def _company_tokens(company):
    return [w for w in ats_match.norm(company).split() if w not in ats_match.SUFFIX and len(w) > 2]


def _good(result, company, title, need_host):
    host = classify.host(result["url"])
    if classify.apply_kind(result["url"]) == "aggregator":
        return False
    if quality.url_problem(result["url"]):
        return False
    page_title = ats_match.norm(result.get("title") or "")
    text = ats_match.norm(" ".join([result.get("title") or "", result.get("snippet") or ""]))
    tokens = _company_tokens(company)
    # the page TITLE must carry the job title (as written, or without the aggregator's Remote/location noise)
    if not tokens or not any(v in page_title for v in ats_match.title_variants(title)):
        return False
    hay = ats_match.norm(host + " " + result["url"]) + " " + text
    if not all(t in hay for t in tokens[:2]):
        return False
    return (not need_host) or tokens[0] in ats_match.norm(host).replace(" ", "")


FAILED = {"tinyfish_search_http_429": "rate_limited", "tinyfish_search_network": "network", "tinyfish_search_unreadable": "unreadable_answer"}


def failed_cause(code):
    """The search error code -> the specific cause shown after `failed_search:`."""
    code = str(code).lower()
    return FAILED.get(code) or (code.replace("tinyfish_search_", "") if code.startswith("tinyfish_search_http_") else "error")


def find(company, title):
    """('hit', kind, url) | ('miss', reason) | ('failed_search', cause). Reasons are fixed codes: `no_result` (nothing
    found), `no_fit` (pages found, none passed the checks). `failed_search` carries the cause (`rate_limited`,
    `network`, `unreadable_answer`, `http_<status>`): not an attempt and never a miss.
    """
    if not company or not title:
        return ("miss", "no_company_or_title")
    try:
        # ONE search per job: any-site hits dominate and a second call doubled time and budget. Each result is judged
        # by the rule for its own kind: an ATS-domain page needs the job title and company; an employer page also
        # needs the company in its host.
        results = tinyfish_search.search(f"{company} {ats_match.title_variants(title)[-1]} careers apply")[:8]
        for result in results:
            on_ats = any(domain in classify.host(result["url"]) for domain in ATS_DOMAINS)
            if _good(result, company, title, need_host=not on_ats):
                return ("hit", classify.apply_kind(result["url"]), result["url"])
    except TinyFishError as error:
        return ("failed_search", failed_cause(error))
    return ("miss", "no_fit" if results else "no_result")
