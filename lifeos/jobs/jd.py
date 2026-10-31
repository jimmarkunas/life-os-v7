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


def _clean(parts):
    lines = (" ".join(line.split()) for line in "".join(parts).split("\n"))
    return "\n".join(line for line in lines if line)


def html_to_text(html):
    parser = _Text()
    parser.feed(html or "")
    return _clean(parser.parts)


VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "wbr"}
LINKEDIN_MARKUP = "show-more-less-html__markup"      # the element LinkedIn's public guest posting page puts the employer's description in


class _Region(_Text):
    """The text of the FIRST element carrying one class, and nothing around it."""

    def __init__(self, marker):
        super().__init__()
        self.marker, self.depth, self.done = marker, 0, False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if self.depth == 0:
            if self.marker in (dict(attrs).get("class") or "").split():
                self.depth = 1
            return
        if tag not in VOID_TAGS:
            self.depth += 1
        super().handle_starttag(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        if self.depth and not self.done and tag in VOID_TAGS:
            super().handle_starttag(tag, attrs)
        elif tag not in VOID_TAGS:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.done or self.depth == 0 or tag in VOID_TAGS:
            return
        self.depth -= 1
        if self.depth == 0:
            self.done = True
        else:
            super().handle_endtag(tag)

    def handle_data(self, data):
        if self.depth and not self.done:
            super().handle_data(data)


def linkedin_description(html):
    """The employer's description from a LinkedIn guest posting page, without the page's sign-in forms, cookie notice and Show more/less chrome.
    None when the page has no such element (the caller keeps its whole-page reading, never a guess); '' when it has one and it is empty, which is an empty
    description, not a cue to read the sign-in text instead."""
    if LINKEDIN_MARKUP not in (html or ""):
        return None
    parser = _Region(LINKEDIN_MARKUP)
    parser.feed(html)
    return _clean(parser.parts)


LINKEDIN_TITLE = "top-card-layout__title"      # LIFE-212 (Jim, Oct 31 2026): the element a LinkedIn guest posting (page or jobPosting fragment) names the role in


def linkedin_title(html):
    """The role as a LinkedIn guest posting names it (its top card's h1/h2/a), or None when the page has no such element. The guest API fragment has no <title>."""
    if LINKEDIN_TITLE not in (html or ""):
        return None
    parser = _Region(LINKEDIN_TITLE)
    parser.feed(html)
    return " ".join(_clean(parser.parts).split()) or None


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
