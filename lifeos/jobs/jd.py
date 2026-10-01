"""Job description handling: HTML -> clean text -> sections (summary / responsibilities / requirements / qualifications).

Pure functions, no network. The full text is ALWAYS kept; sections are a best-effort split by headings, so a
posting with unusual headings still has its complete text available for scoring. Stored only in Hostinger.
"""
from html.parser import HTMLParser
from hashlib import sha256
import re

SECTION_PATTERNS = (   # first match wins; order matters (specific before generic)
    ("responsibilities", r"responsibilit|what you.?ll do|what you will do|duties|day.to.day|your impact|in this role|key tasks"),
    ("requirements", r"requirement|must[- ]have|minimum|basic qualifications|required|what you.?ll bring|what you bring|what we.?re looking for|you have|you.?ll need|who you are|about you"),
    ("qualifications", r"qualification|preferred|nice to have|bonus|skills|experience|education|plus|desired"),
    ("summary", r"about (the )?(role|job|position|us|company|team)|overview|summary|job description|the role|description|who we are|position"),
)
BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html):
    parser = _Text()
    parser.feed(html or "")
    lines = (" ".join(line.split()) for line in "".join(parser.parts).split("\n"))
    return "\n".join(line for line in lines if line)


def _heading_kind(line):
    bare = line.strip(" :*-•").lower()
    if not bare or len(bare) > 70 or (len(line.split()) > 9):
        return None
    for kind, pattern in SECTION_PATTERNS:
        if re.search(pattern, bare):
            return kind
    return None


def split_sections(text):
    """Return {'summary','responsibilities','requirements','qualifications'} -> text ('' when absent)."""
    sections = {"summary": [], "responsibilities": [], "requirements": [], "qualifications": []}
    current = "summary"
    for line in (text or "").split("\n"):
        kind = _heading_kind(line) if not line.startswith("•") else None
        if kind:
            current = kind
            continue
        sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def describe(html_or_text, is_html=True):
    text = html_to_text(html_or_text) if is_html else (html_or_text or "")
    return {"full_text": text, "fingerprint": sha256(text.encode()).hexdigest(), **split_sections(text)}
