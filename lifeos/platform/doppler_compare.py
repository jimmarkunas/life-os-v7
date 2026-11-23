"""LIFE-863: the Doppler trial. Read the same secret names from Doppler (project life-os, config prd, one read-only service token) and compare each with the value the job already loaded from Bitwarden.

    python -m lifeos.platform.doppler_compare        env: DOPPLER_TOKEN, DOPPLER_PROJECT, DOPPLER_CONFIG, COMPARE_NAMES (comma list), BITWARDEN (JSON object name -> value, from the masked Bitwarden step outputs)

Bitwarden stays the source of every value the job uses; this only compares. Values are compared by SHA-256 in memory and are never printed; Doppler's values are masked. Output: one `doppler-compare: {...}` line with counts, NAMES (not values), a fixed code and the
Doppler read time, plus the step outputs `code`, `matched`, `total`, `doppler_ms`. Fixed codes: match, mismatch, missing_in_doppler, missing_in_bitwarden, doppler_unauthorized, doppler_unreachable, doppler_bad_reply, bad_input. A problem is a code and exit 0: the job
keeps running on Bitwarden. Stdlib only."""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

URL = "https://api.doppler.com/v3/configs/config/secrets/download"
TIMEOUT_SECONDS = 20


def fetch(token, project, config, timeout=TIMEOUT_SECONDS):
    """-> (code, {name: value} or None). Doppler's download endpoint with the service token; no value is ever returned in a code."""
    query = urllib.parse.urlencode({"format": "json", "project": project, "config": config})
    request = urllib.request.Request(f"{URL}?{query}", headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:                    # noqa: S310 - a fixed https URL
            data = json.loads(reply.read().decode())
    except urllib.error.HTTPError as error:
        return ("doppler_unauthorized" if error.code in (401, 403) else "doppler_unreachable"), None
    except (urllib.error.URLError, OSError, TimeoutError):
        return "doppler_unreachable", None
    except ValueError:
        return "doppler_bad_reply", None
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        return "doppler_bad_reply", None
    return "ok", data


def digest(value):
    return hashlib.sha256(value.encode()).digest()


def compare(names, bitwarden, doppler):
    """-> {"matched": [...], "mismatched": [...], "missing_in_doppler": [...], "missing_in_bitwarden": [...]} by name; equality by hash."""
    out = {"matched": [], "mismatched": [], "missing_in_doppler": [], "missing_in_bitwarden": []}
    for name in names:
        have_b = isinstance(bitwarden.get(name), str) and bitwarden[name] != ""
        have_d = isinstance(doppler.get(name), str) and doppler[name] != ""
        if not have_b:
            out["missing_in_bitwarden"].append(name)
        elif not have_d:
            out["missing_in_doppler"].append(name)
        elif hmac.compare_digest(digest(bitwarden[name]), digest(doppler[name])):
            out["matched"].append(name)
        else:
            out["mismatched"].append(name)
    return out


def main(environ=os.environ, fetcher=fetch, clock=time.monotonic, out=print):
    names = [n.strip() for n in str(environ.get("COMPARE_NAMES", "")).split(",") if n.strip()]
    token, project, config = environ.get("DOPPLER_TOKEN", ""), environ.get("DOPPLER_PROJECT", ""), environ.get("DOPPLER_CONFIG", "")
    try:
        bitwarden = json.loads(environ.get("BITWARDEN") or "{}")
        if not isinstance(bitwarden, dict):
            raise ValueError("not an object")
    except ValueError:
        bitwarden = None
    result = {"code": "bad_input", "total": len(names), "matched": 0, "mismatched": [], "missing_in_doppler": [], "missing_in_bitwarden": [], "doppler_ms": None}
    if names and token and project and config and bitwarden is not None:
        started = clock()
        status, doppler = fetcher(token, project, config)
        result["doppler_ms"] = round((clock() - started) * 1000)
        if status != "ok":
            result["code"] = status
        else:
            for value in doppler.values():
                if value:
                    out(f"::add-mask::{value}")                                           # the runner masks it from here on; nothing prints it
            found = compare(names, bitwarden, doppler)
            result.update(matched=len(found["matched"]), mismatched=found["mismatched"], missing_in_doppler=found["missing_in_doppler"], missing_in_bitwarden=found["missing_in_bitwarden"])
            result["code"] = "match" if result["matched"] == len(names) else "missing_in_bitwarden" if found["missing_in_bitwarden"] else "missing_in_doppler" if found["missing_in_doppler"] else "mismatch"
    out("doppler-compare: " + json.dumps(result, sort_keys=True))
    lines = f"code={result['code']}\nmatched={result['matched']}\ntotal={result['total']}\ndoppler_ms={result['doppler_ms'] if result['doppler_ms'] is not None else ''}\n"
    summary = f"### Doppler trial (LIFE-863)\n\n- Code: {result['code']}\n- Matched: {result['matched']} of {result['total']}\n- Doppler read: {result['doppler_ms']} ms\n"
    for path, content in ((environ.get("GITHUB_OUTPUT"), lines), (environ.get("GITHUB_STEP_SUMMARY"), summary)):
        if path:
            with open(path, "a") as handle:
                handle.write(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
