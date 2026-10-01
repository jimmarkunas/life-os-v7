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


def reason(job, today=None):
    """A fixed reason code when the posting is an unequivocal miss for US Remote, else None."""
    title, where = job["title"], job["location"]
    if not TARGET.search(title) and OFF.search(title):
        return "off_target_title"
    if NON_US.search(where) and not US.search(where):
        return "non_us"
    if lanes.detect_work_mode(where, title) in ("onsite", "hybrid"):
        return "not_remote"
    policy = lanes.POLICIES["US Remote"]
    if job["posted"] and ((today or date.today()) - job["posted"]).days > policy.max_age_days:
        return "stale"
    return None
