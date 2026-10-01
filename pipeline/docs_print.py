"""Print readable text of ONE public docs page (allowlisted host) so API shapes can be read from CI logs."""
import os
import sys
from urllib.parse import urlsplit

from pipeline import jd
from pipeline.http import fetch

ALLOWED = {"docs.tinyfish.ai", "www.tinyfish.ai"}


def main():
    url = os.environ.get("DOCS_URL", "")
    if (urlsplit(url).hostname or "") not in ALLOWED:
        print("docs_print: host not allowlisted", file=sys.stderr)
        return 1
    page = fetch(url, timeout=20)
    text = page.html if url.endswith((".txt", ".md")) else jd.html_to_text(page.html)
    print(f"docs_print status={page.status} chars={len(text)}")
    print(text[:9000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
