"""Stable hash of one policy section, used to pin rules to the text they came from."""

import hashlib
import re

_FRONTMATTER = re.compile(r"\A---\n.*?\n---(?:\n|\Z)", re.DOTALL)
_NEXT_SECTION = re.compile(r"^## ", re.MULTILINE)


def document_body(text: str) -> str:
    """Return the text with LF line endings and the frontmatter removed."""
    return _FRONTMATTER.sub("", text.replace("\r\n", "\n").replace("\r", "\n"), count=1)


def _section_text(text: str, n: int) -> str:
    body = document_body(text)
    if n == 0:
        return body
    heading = re.search(rf"^## {n}\.(?:\s|$)", body, re.MULTILINE)
    if heading is None:
        # A missing section must never hash to a value that could pass as pinned.
        raise ValueError(f"section {n} not found")
    following = _NEXT_SECTION.search(body, heading.end())
    return body[heading.start() : following.start() if following else len(body)]


def section_hash(text: str, n: int) -> str:
    """Return the SHA-256 hex of section n (0 is the whole body), with whitespace collapsed."""
    normalised = " ".join(_section_text(text, n).split())
    return hashlib.sha256(normalised.encode()).hexdigest()
