"""Shared helper: collect (href, [text nodes]) for every <a> whose href matches a pattern. Sloppy-HTML tolerant."""
from html.parser import HTMLParser


class Anchors(HTMLParser):
    def __init__(self, href_pattern):
        super().__init__(convert_charrefs=True)
        self.pattern, self.found, self._cur, self._depth = href_pattern, [], None, 0

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        if self._cur is not None:
            self._depth += 1
        elif self.pattern.search(href):
            self._cur, self._depth = {"href": href, "texts": []}, 1

    def handle_endtag(self, tag):
        if tag == "a" and self._cur is not None:
            self._depth -= 1
            if self._depth <= 0:
                self.found.append(self._cur)
                self._cur = None

    def handle_data(self, data):
        text = " ".join(data.split())
        if text and self._cur is not None:
            self._cur["texts"].append(text)

    def close(self):
        super().close()
        if self._cur is not None:
            self.found.append(self._cur)
            self._cur = None


def collect(html, href_pattern):
    parser = Anchors(href_pattern)
    parser.feed(html or "")
    parser.close()
    return parser.found
