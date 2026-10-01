"""Find the employer's own posting through web search (free TinyFish Search), precision first.
Attempt 1: ATS domains only. Attempt 2: any non-aggregator site whose host carries the company name.
Accept only when the result title contains the job title AND the company name appears in host/title/snippet."""
from pipeline import ats_match, classify, quality, tinyfish_search
from pipeline.tinyfish import TinyFishError

ATS_DOMAINS = ("greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "icims.com", "smartrecruiters.com",
               "workable.com", "jobvite.com", "bamboohr.com", "recruitee.com", "applytojob.com", "teamtailor.com",
               "taleo.net", "successfactors.com", "oraclecloud.com", "paylocity.com", "ultipro.com", "dayforcehcm.com")


def _company_tokens(company):
    return [w for w in ats_match.norm(company).split() if w not in ats_match.SUFFIX and len(w) > 2]


def _good(result, company, title, need_host):
    host = classify.host(result["url"])
    if classify.apply_kind(result["url"]) == "aggregator":
        return False
    if quality.url_problem(result["url"]):                  # search/listing pages are not the job
        return False
    page_title = ats_match.norm(result.get("title") or "")
    text = ats_match.norm(" ".join([result.get("title") or "", result.get("snippet") or ""]))
    tokens = _company_tokens(company)
    if not tokens or ats_match.norm(title) not in page_title:    # the page TITLE must carry the job title
        return False
    hay = ats_match.norm(host + " " + result["url"]) + " " + text
    if not all(t in hay for t in tokens[:2]):
        return False
    return (not need_host) or tokens[0] in ats_match.norm(host).replace(" ", "")


def find(company, title):
    """('hit', kind, url) | ('miss', reason). Reasons are fixed codes."""
    if not company or not title:
        return ("miss", "no_company_or_title")
    try:
        for domains, need_host, kind_hint in ((ATS_DOMAINS, False, "ats"), (None, True, "employer")):
            query = f"{company} {title}" if domains else f"{company} {title} careers apply"
            for result in tinyfish_search.search(query, domains)[:8]:
                if _good(result, company, title, need_host):
                    return ("hit", classify.apply_kind(result["url"]), result["url"])
    except TinyFishError as error:
        return ("miss", str(error).lower())
    return ("miss", "no_result")
