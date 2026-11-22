"""Deterministic tokenizer (decision log IX): IDs stay whole, numbers stay exact, no stemming."""

import re
import unicodedata

STOPWORDS = frozenset(
    "a an and are as at be by for from in is it of on or that the this to was were will with".split()
)

_TOKEN = re.compile(
    r"[a-z]{2,}(?:-[a-z]{2,})*-\d+(?:-[a-z0-9]+)*"  # document, invoice and PO IDs
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?%?(?!\w)"  # numbers with thousands separators
    r"|\d+(?:\.\d+)?%?(?!\w)"
    r"|\w+"
)


def _normalise(text: str) -> str:
    characters = []
    for character in unicodedata.normalize("NFKC", text):
        category = unicodedata.category(character)
        if category == "Cf":
            continue
        characters.append("-" if category == "Pd" else character)
    return "".join(characters).lower()


def tokenize(text: str) -> list[str]:
    tokens = (match.group(0).replace(",", "") for match in _TOKEN.finditer(_normalise(text)))
    return [token for token in tokens if token not in STOPWORDS]
