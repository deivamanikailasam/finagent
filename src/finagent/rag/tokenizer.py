"""Deterministic tokenizer (decision log IX): IDs stay whole, numbers stay exact, no stemming."""

import re
import unicodedata

STOPWORDS = frozenset(
    "a an and are as at be by for from in is it of on or that the this to was were will with".split()
)

_CURRENCIES = "aud|usd|nzd|eur|gbp"
_CURRENCY_BEFORE_NUMBER = re.compile(rf"\b({_CURRENCIES})(?=[0-9])")
_CURRENCY_AFTER_NUMBER = re.compile(rf"(?<=[0-9])({_CURRENCIES})\b")
_TOKEN = re.compile(
    r"[a-z]{2,}(?:-[a-z]{2,})*-[0-9]+[a-z0-9]*(?:-[a-z0-9]+)*"  # document, invoice and PO IDs
    r"|(?<![\w.,])\.[0-9]+%?(?!\w)"  # a leading-dot number such as .5%
    r"|(?<![\w.])[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?%?(?!\w)"  # numbers with thousands separators
    r"|(?<![\w.])[0-9]+(?:\.[0-9]+)?%?(?!\w)"
    r"|\w+"
)


def _normalise(text: str) -> str:
    characters = []
    for character in unicodedata.normalize("NFKC", text):
        category = unicodedata.category(character)
        if category == "Cf":
            continue
        if category == "Pd" or character == "−":  # U+2212 is a maths minus, not a dash category
            characters.append("-")
        elif category == "Nd":
            characters.append(str(unicodedata.digit(character)))
        else:
            characters.append(character)
    text = "".join(characters).lower()
    text = _CURRENCY_BEFORE_NUMBER.sub(r"\1 ", text)
    return _CURRENCY_AFTER_NUMBER.sub(r" \1", text)


def _canonical(token: str) -> str:
    token = token.replace(",", "")
    return f"0{token}" if token.startswith(".") else token  # .5% and 0.5% are the same threshold


def tokenize(text: str) -> list[str]:
    tokens = (_canonical(match.group(0)) for match in _TOKEN.finditer(_normalise(text)))
    return [token for token in tokens if token not in STOPWORDS]
