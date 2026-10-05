"""NET-1b identity and position classification. Pure: rows in, plain records and counts out; nothing is read from or written to anywhere.

A person exists only when identity is deterministic: a valid, unique LinkedIn profile address. A row with no address is HELD (counted, never guessed or dropped), a row whose
address is not a profile address is REJECTED, and rows that share one profile address are HELD together (conflicting evidence is never silently resolved).
A person's position is independent of the person: a usable employer gives one, a blank or non-employer company gives none. The title may be blank (stored as NULL).
Email is never read here, so it can never be stored."""
from collections import Counter
import hashlib

from lifeos.network import parse


def person_key(url_key):
    return hashlib.sha256(("person|" + url_key).encode()).hexdigest()


def classify(rows, malformed=0):
    """-> {people: [record sorted by url_key], source_rows, ambiguous_held, rejected, positions}. record: url_key, name, connected_on, company_key, company_name, title (None or text).
    company_key is None when the person has no usable employer."""
    keys = [parse.url_key(r["url"]) for r in rows]
    shared = Counter(k for k in keys if k)
    people, held, rejected = [], 0, malformed
    for row, key in zip(rows, keys):
        if not row["url"].strip():
            held += 1
        elif key is None:
            rejected += 1
        elif shared[key] > 1:
            held += 1
        else:
            ckey = parse.company_key(row["company"])
            usable = bool(ckey) and not parse.not_an_employer(row["company"], row["first"], row["last"])
            people.append({"url_key": key, "name": f"{row['first']} {row['last']}".strip(), "connected_on": parse.parse_date(row["connected"]),
                           "company_key": ckey if usable else None, "company_name": row["company"].strip() if usable else None, "title": row["title"].strip() or None})
    people.sort(key=lambda p: p["url_key"])
    out = {"people": people, "source_rows": len(rows), "ambiguous_held": held, "rejected": rejected - malformed,
           "positions": sum(1 for p in people if p["company_key"])}
    assert out["source_rows"] == len(people) + held + out["rejected"]
    return out


def batch_key(plan, exported):
    """The identity of the batch consumed: the observation date and every (person, employer, title) it carries. The same file always has the same key."""
    digest = hashlib.sha256(f"roster|{exported}|{plan['ambiguous_held']}|{plan['rejected']}".encode())
    for p in plan["people"]:
        digest.update(f"|{p['url_key']}|{p['company_key']}|{p['title']}".encode())
    return digest.hexdigest()


def material_hash(url_key, company_key, title, source_kind, observed):
    """One position as observed on one date: a person who returns to an earlier employer still gets a new row."""
    return hashlib.sha256(f"{url_key}|{company_key}|{title or ''}|{source_kind}|{observed}".encode()).hexdigest()
