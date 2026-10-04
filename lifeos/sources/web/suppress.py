"""Cheap suppression BEFORE any description, Fit or Notion work. Only unequivocal misses are dropped: a title that names the target
family always continues (so "Technical Program Manager, DevOps Platform" is never killed by "DevOps"), and anything ambiguous goes on to Fit."""
from datetime import date
import re

from lifeos.jobs import lanes
from lifeos.jobs.fit import lexicon

TARGET = re.compile(r"\b(program|programme|project|product|delivery|implementation|transformation|scrum|tpm|engagement|"
                    r"solutions? (?:architect|consultant|engineer)|operations|strategy|chief of staff)\b", re.I)
OFF = re.compile(r"\b(" + "|".join(re.escape(t) for t in (
    *lexicon.HARD_FAMILY, *lexicon.OFF_TARGET, "engineer", "developer", "sdet", "designer", "recruiter", "sourcer", "paralegal", "attorney",
    "counsel", "account executive", "sales development", "business development representative", "accountant", "payroll", "nurse",
    "technician", "driver", "warehouse", "barista", "cashier")) + r")\b", re.I)
NON_US = re.compile(r"\b(canada|toronto|vancouver|montreal|ontario|united kingdom|\buk\b|london|england|ireland|dublin|germany|berlin|munich|"
                    r"france|paris|spain|madrid|barcelona|netherlands|amsterdam|poland|warsaw|india|bangalore|bengaluru|hyderabad|pune|mumbai|"
                    r"australia|sydney|melbourne|singapore|japan|tokyo|brazil|sao paulo|mexico|israel|tel aviv|philippines|ukraine|portugal|lisbon)\b", re.I)
US = re.compile(r"\b(united states|usa|u\.s\.|us)\b|(?:,\s*|\s)(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b")


def reason(job, today=None, lane="US Remote", first_party=False):
    """A fixed reason code when the posting is an unequivocal miss for the lane, else None. first_party: the job is on its employer's own board (D111): being listed there
    shows it is open, so age is not a reason."""
    title, where = job["title"], job["location"]
    policy = lanes.POLICIES[lane]
    if not TARGET.search(title) and OFF.search(title):
        return "off_target_title"
    if policy.market == "US":
        if NON_US.search(where) and not US.search(where):
            return "non_us"
        if policy.work_mode == "remote_only" and lanes.detect_work_mode(where, title) in ("onsite", "hybrid"):
            return "not_remote"
    elif lanes.geography_status(where) == lanes.NEGATIVE:
        return "non_target_geography"                   # Scale-Up: any work mode, but a named non-target place is out
    if policy.max_age_days and not first_party and job["posted"] and ((today or date.today()) - job["posted"]).days > policy.max_age_days:
        return "stale"
    return None
