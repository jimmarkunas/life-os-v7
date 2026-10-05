"""Read a LinkedIn connections export and measure it. Pure functions over text: no I/O, no network, nothing stored.

The export is a CSV with LinkedIn's standard columns (first name, last name, profile URL, email address, company, position, connected on),
usually preceded by a few lines of notes. Columns are found by heading, not position. Every function here returns counts or keys; a person's
name, company and address are used in memory to compare rows and are never returned."""
import csv
import io
import re
from datetime import datetime
from urllib.parse import unquote

from lifeos.platform import names

from lifeos.network.errors import NetworkError

HEADINGS = {"first name": "first", "last name": "last", "url": "url", "email address": "email", "company": "company",
            "position": "title", "connected on": "connected"}
REQUIRED = ("first", "last", "url", "company", "title", "connected")
# Company values that name no employer. Exact phrases only: "Independent Bank" is an employer, "Independent" is not.
NO_EMPLOYER = {"freelance", "freelancer", "freelance consultant", "self employed", "self-employed", "independent", "independent consultant",
               "independent contractor", "consultant", "contractor", "retired", "student", "unemployed", "stealth", "stealth startup",
               "confidential", "n/a", "na", "none", "seeking opportunities", "open to work"}
PROFILE = re.compile(r"linkedin\.com/in/([^/]+)")


def _squash(text):
    return " ".join(names.norm(text).split())


def url_key(url):
    """The identity of a profile: the part after /in/, lower-cased, with scheme, country prefix, query, fragment and trailing slash removed.
    None when the address is blank or is not a profile address."""
    value = unquote((url or "").strip()).lower()
    if not value:
        return None
    value = re.sub(r"^https?://", "", value)
    value = re.sub(r"^(www\.|[a-z]{2}\.)(?=linkedin\.com)", "", value)
    value = value.split("?")[0].split("#")[0].rstrip("/")
    match = re.fullmatch(PROFILE.pattern, value)
    return match.group(1) if match else None


def company_key(name):
    """The distinctive tokens of an employer name, joined ("Acme Technologies, Inc." gives "acme"). Empty when there is nothing to key on."""
    return " ".join(names.core(name))


def not_an_employer(company, first, last):
    """True when the company value is the person's own name or wording such as freelance or self-employed."""
    value = _squash(company)
    if not value:
        return False
    if value in NO_EMPLOYER or value.startswith(("freelance ", "self employed ", "self-employed ")):
        return True
    person = _squash(f"{first} {last}")
    return bool(person) and value == person


def parse_date(text):
    try:
        return datetime.strptime((text or "").strip(), "%d %b %Y").date()
    except ValueError:
        return None


def read_rows(text):
    """-> (rows, malformed). rows: dicts keyed by field. Raises NETWORK_NO_HEADER when no heading row is found, so a wrong file fails closed."""
    reader = csv.reader(io.StringIO((text or "").lstrip("﻿")))
    index, rows, malformed = None, [], 0
    for cells in reader:
        if index is None:
            lowered = [c.strip().lower() for c in cells]
            if "first name" in lowered and "url" in lowered and "company" in lowered:
                index = {HEADINGS[h]: i for i, h in enumerate(lowered) if h in HEADINGS}
                if any(f not in index for f in REQUIRED):
                    raise NetworkError("NETWORK_NO_HEADER")
                width = len(cells)
            continue
        if not any(c.strip() for c in cells):
            continue
        if len(cells) != width:
            malformed += 1
            continue
        rows.append({field: cells[i].strip() for field, i in index.items()})
    if index is None:
        raise NetworkError("NETWORK_NO_HEADER")
    return rows, malformed


def analyse(rows, malformed=0):
    """Counts only. 'matchable' rows have a valid profile key, an employer, and are not flagged as having no employer."""
    keys, blank_url, bad_url, no_employer = {}, 0, 0, 0
    blank_company = blank_title = blank_date = bad_date = emails = matchable = 0
    companies, loose, dates = set(), {}, []
    for row in rows:
        key = url_key(row["url"])
        if not row["url"].strip():
            blank_url += 1
        elif key is None:
            bad_url += 1
        if key:
            keys[key] = keys.get(key, 0) + 1
        ckey = company_key(row["company"])
        flagged = not_an_employer(row["company"], row["first"], row["last"])
        blank_company += not row["company"].strip()
        blank_title += not row["title"].strip()
        no_employer += flagged
        emails += bool(row.get("email", "").strip())
        if not row["connected"].strip():
            blank_date += 1
        else:
            parsed = parse_date(row["connected"])
            if parsed is None:
                bad_date += 1
            else:
                dates.append(parsed)
        if ckey and not flagged:
            companies.add(ckey)
            matchable += bool(key)
        if key is None:                                           # no profile key: identity would fall back to name plus employer
            fallback = (_squash(f"{row['first']} {row['last']}"), ckey)
            loose[fallback] = loose.get(fallback, 0) + 1
    out = {"rows": len(rows), "malformed_rows": malformed, "blank_url": blank_url, "invalid_url": bad_url,
           "duplicate_url_keys": sum(1 for n in keys.values() if n > 1), "blank_company": blank_company, "blank_title": blank_title,
           "no_employer_company": no_employer, "email_present": emails, "blank_connected_on": blank_date, "bad_connected_on": bad_date,
           "distinct_company_keys": len(companies), "matchable_rows": matchable,
           "ambiguous_no_url_rows": sum(n for n in loose.values() if n > 1)}
    if dates:
        out["newest_connected_on"], out["oldest_connected_on"] = max(dates).isoformat(), min(dates).isoformat()
    return out
