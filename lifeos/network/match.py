"""NET-2.2: match a job's employer to the stored network, from stored state only. Pure: no database, no network, no names; the caller supplies the rows.

Company identity is the normalized company key the importer stores (the distinctive tokens of the employer name). A lead is a person with a stored position at that company:
tier CURRENT (the latest listed position), tier PREVIOUS (a superseded position there: "previously listed"), tier POSSIBLE (no key is equal but one key is a whole-word prefix of the
other, such as "meta" and "meta platforms"; shown last and never auto-confirmed). Within a tier: title-word overlap with the job's title, then the freshest observation, then a stable id.
Nobody with no company match is ever shown, so weak signals cannot fill slots, and at most MAX_LEADS are returned. Nothing about closeness, influence, willingness to refer or
hiring authority is inferred; freshness is only the age of the observation (FRESH up to 45 days, AGING to 120, STALE beyond)."""
from datetime import date

from lifeos.platform import names

CURRENT, PREVIOUS, POSSIBLE = "CURRENT", "PREVIOUS", "POSSIBLE"
TIERS = (CURRENT, PREVIOUS, POSSIBLE)
FRESH_DAYS, AGING_DAYS, MAX_LEADS = 45, 120, 5
ROLE_STOP = {"and", "the", "for", "of", "senior", "sr", "junior", "jr", "lead", "head", "principal", "staff", "associate"}


def freshness(age_days):
    return "FRESH" if age_days <= FRESH_DAYS else "AGING" if age_days <= AGING_DAYS else "STALE"


def _words(title):
    return {t for t in names.norm(title).split() if len(t) > 2 and t not in ROLE_STOP}


def build_index(rows):
    """rows of (person_id, company_key, title, position_state, last_verified: date) -> {company_key: [(person_id, tier, title_words, last_verified)]}. A blank company key is dropped."""
    index = {}
    for person_id, company_key, title, state, verified in rows:
        if not company_key:
            continue
        tier = CURRENT if state == "CURRENT" else PREVIOUS
        index.setdefault(company_key, []).append((person_id, tier, _words(title), verified))
    return index


def _prefix(a, b):
    """True when one key is a whole-word prefix of the other and they are not equal."""
    x, y = a.split(), b.split()
    short, long_ = (x, y) if len(x) <= len(y) else (y, x)
    return a != b and bool(short) and long_[:len(short)] == short


def leads_for(company_key, job_title, index, today):
    """-> up to MAX_LEADS leads, best first: {person_id, tier, freshness, age_days, overlap}. Each person appears once, at their best tier."""
    if not company_key:
        return []
    wanted = _words(job_title)
    best = {}
    for key, entries in index.items():
        if key == company_key:
            kind = None
        elif _prefix(key, company_key):
            kind = POSSIBLE
        else:
            continue
        for person_id, tier, words, verified in entries:
            tier = kind or tier
            rank = TIERS.index(tier)
            lead = {"person_id": person_id, "tier": tier, "age_days": max((today - verified).days, 0), "overlap": len(wanted & words), "_verified": verified}
            if person_id not in best or rank < TIERS.index(best[person_id]["tier"]) or (rank == TIERS.index(best[person_id]["tier"]) and verified > best[person_id]["_verified"]):
                best[person_id] = lead
    ordered = sorted(best.values(), key=lambda l: (TIERS.index(l["tier"]), -l["overlap"], l["age_days"], l["person_id"]))
    for lead in ordered:
        lead["freshness"] = freshness(lead["age_days"])
        del lead["_verified"]
    return ordered[:MAX_LEADS]
