"""Classify a board listing against the accepted state: NEW, MATERIAL_CHANGE, UNCHANGED and (only after a COMPLETE listing) REMOVED."""
from hashlib import sha256


def material_hash(job):
    """Title, location, url, content and posted date are the material fields; first-seen and crawl time are not."""
    raw = "\x1f".join([job["title"], job["location"], job["url"], job["content"] or "", job["posted"].isoformat() if job["posted"] else ""])
    return sha256(raw.encode()).hexdigest()


def classify(previous, jobs, complete):
    """previous: {provider job id: material hash}. -> (new, changed, unchanged, removed); removed is [] unless `complete`."""
    new, changed, unchanged, seen = [], [], [], set()
    for job in jobs:
        seen.add(job["id"])
        digest = material_hash(job)
        if job["id"] not in previous:
            new.append((job, digest))
        elif previous[job["id"]] != digest:
            changed.append((job, digest))
        else:
            unchanged.append(job["id"])
    removed = [i for i in previous if i not in seen] if complete else []
    return new, changed, unchanged, removed


def frontier(items):
    """One hash over the accepted inventory, so an unchanged board is recognisable at a glance."""
    return sha256("\n".join(f"{i}:{h}" for i, h in sorted(items.items())).encode()).hexdigest()
