"""Where does a final apply URL live? employer site > employer's ATS > aggregator (by host only)."""
from urllib.parse import urlsplit

ATS = ("greenhouse.io", "ashbyhq.com", "lever.co", "myworkdayjobs.com", "myworkdaysite.com", "smartrecruiters.com",
       "icims.com", "jobvite.com", "workable.com", "bamboohr.com", "taleo.net", "successfactors.com", "successfactors.eu",
       "dayforcehcm.com", "paylocity.com", "ultipro.com", "recruitee.com", "breezy.hr", "jazz.co", "applytojob.com",
       "rippling.com", "oraclecloud.com", "paycomonline.net", "teamtailor.com", "pinpointhq.com", "brassring.com",
       "jobs.gusto.com", "hire.lever.co", "eightfold.ai", "phenompeople.com", "avature.net")
AGGREGATORS = ("lensa.com", "jobright.ai", "linkedin.com", "indeed.com", "ziprecruiter.com", "dice.com", "glassdoor.com",
               "simplyhired.com", "talent.com", "jooble.org", "adzuna.com", "monster.com", "careerbuilder.com",
               "whatjobs.com", "jobleads.com", "recruit.net", "jobrapido.com", "snagajob.com", "flexjobs.com",
               "remotive.com", "builtin.com", "wellfound.com", "ladders.com", "upwork.com", "learn4good.com",
               "jobilize.com", "jobs2careers.com", "neuvoo.com", "salary.com", "careerjet.com", "google.com")


def host(url):
    return (urlsplit(url).hostname or "").lower()


def _matches(h, domains):
    return any(h == d or h.endswith("." + d) for d in domains)


def apply_kind(url):
    """'ats' | 'aggregator' | 'employer' for a resolved destination URL."""
    h = host(url)
    if _matches(h, ATS):
        return "ats"
    if _matches(h, AGGREGATORS):
        return "aggregator"
    return "employer"
