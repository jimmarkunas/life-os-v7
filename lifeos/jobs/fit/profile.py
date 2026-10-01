"""The private fit profile: injected at run time, validated here, never committed (D16).

Shape (JSON, held in the FIT_PROFILE_JSON secret):
  {"years": 20,
   "capabilities": [{"id": "x", "label": "short strength label", "class": "direct|adjacent|method",
                     "terms": ["lower-case words or phrases"], "bucket": "platform|skill", "committed": true}],
   "functions": [{"terms": ["..."], "class": "direct|adjacent|method"}],       # role families / duties
   "scope": {"team_size": 12, "budget_usd": 50000000, "global": "direct", "enterprise": "direct", ...},
   "exclusions": [{"id": "", "reason": "", "terms": [], "patterns": [], "where": "title|company|any", "strict": false}],
   "advantage": ["ai", "automation"], "specialization": ["technical program manager"], "hard_family": [],
   "baseline": true}
`committed` capabilities are the user's corrections: they are always Direct, whatever `class` says.
"""
from hashlib import sha256
import json
import re

from lifeos.platform.names import norm          # noqa: F401 - one normalizer, shared with Interview OS

VALUE = {"direct": 1.0, "adjacent": 0.75, "method": 0.6, "unsupported": 0.0}   # V2/V3 arithmetic: 1, 3/4, 3/5, 0
RANK = {"unsupported": 0, "method": 1, "adjacent": 2, "direct": 3}


class ProfileError(ValueError):
    pass


def term_regex(term):
    t = re.escape(norm(term).strip())
    return re.compile(rf"(?<![a-z0-9]){t}(?:s|es)?(?![a-z0-9])")


class Profile:
    def __init__(self, data):
        if not isinstance(data, dict):
            raise ProfileError("profile must be an object")
        self.years = int(data.get("years") or 0)
        self.scope = dict(data.get("scope") or {})
        self.baseline = bool(data.get("baseline", True))
        self.advantage = [term_regex(t) for t in data.get("advantage") or []]          # competitive-advantage terms (AI, ...)
        self.specialization = [term_regex(t) for t in data.get("specialization") or []]  # a direct title specialization: +3
        self.hard_family = [term_regex(t) for t in data.get("hard_family") or []]        # private additions to the generic list
        self.exclusions = list(data.get("exclusions") or [])
        self.capabilities, self.functions = [], []
        for row in data.get("capabilities") or []:
            cls = "direct" if row.get("committed") else row.get("class")
            if cls not in VALUE or not row.get("terms"):
                raise ProfileError(f"capability {row.get('id')!r}: needs a valid class and terms")
            self.capabilities.append({"id": row.get("id") or row["terms"][0], "label": row.get("label") or row["terms"][0],
                                      "class": cls, "bucket": row.get("bucket") or "platform",
                                      "rx": [term_regex(t) for t in row["terms"]], "terms": row["terms"]})
        for row in data.get("functions") or []:
            if row.get("class") not in VALUE or not row.get("terms"):
                raise ProfileError("function: needs a valid class and terms")
            self.functions.append({"class": row["class"], "label": row["terms"][0], "terms": row["terms"],
                                   "rx": [term_regex(t) for t in row["terms"]]})
        for row in self.exclusions:
            if not (row.get("terms") or row.get("patterns")):
                raise ProfileError("exclusion: needs terms or patterns")
        self.hash = sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]

    @staticmethod
    def _best(rows, text):
        found = None
        for row in rows:
            if any(rx.search(text) for rx in row["rx"]) and (found is None or RANK[row["class"]] > RANK[found["class"]]):
                found = row
        return found

    def capability(self, text, bucket=None):
        """Best capability whose terms appear in the (normalized) text."""
        return self._best([c for c in self.capabilities if bucket in (None, c["bucket"])], text)

    def function(self, text):
        return self._best(self.functions, text)


def load(environ):
    """The profile from the FIT_PROFILE_JSON secret; ProfileError when it is absent or invalid."""
    raw = (environ.get("FIT_PROFILE_JSON") or "").strip()
    if not raw:
        raise ProfileError("FIT_PROFILE_JSON is not set")
    try:
        return Profile(json.loads(raw))
    except (ValueError, TypeError, KeyError) as error:
        raise ProfileError("FIT_PROFILE_JSON is not a valid profile") from error
