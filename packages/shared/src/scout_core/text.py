"""Text helpers: HTML to plain text, whitespace and unicode cleanup."""

import html
import re
import unicodedata

from selectolax.lexbor import LexborHTMLParser as HTMLParser

_WS = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def html_to_text(raw: str | None) -> str | None:
    """Convert (possibly entity-escaped) HTML to readable plain text.

    Greenhouse returns descriptions as escaped HTML ("&lt;p&gt;..."), so unescape first.
    """
    if not raw:
        return None
    unescaped = html.unescape(raw)
    if "<" not in unescaped:
        return clean_whitespace(unescaped)
    tree = HTMLParser(unescaped)
    for node in tree.css("script, style"):
        node.decompose()
    for node in tree.css("br, p, div, li, h1, h2, h3, h4, h5, h6, tr"):
        node.insert_after("\n")
    body = tree.body or tree.root
    text = body.text(separator="") if body else ""
    return clean_whitespace(text)


def clean_whitespace(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    lines = [_WS.sub(" ", line).strip() for line in text.splitlines()]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def fold(text: str | None) -> str:
    """Lowercase, NFKC-normalised, single-spaced text for matching."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    return re.sub(r"\s+", " ", text).strip()
