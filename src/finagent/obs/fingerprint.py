"""Keyed fingerprints, so sensitive values can be compared without being stored."""

import hashlib
import hmac
import re
import unicodedata

from pydantic import SecretStr

_ACCOUNT_KIND = "account"


def _normalise(kind: str, value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    if kind == _ACCOUNT_KIND:
        digits = re.sub(r"\D", "", text)
        if not digits.isascii():
            # Dropping a non-ASCII digit would let two different accounts share a fingerprint.
            raise ValueError("account contains non-ASCII digits")
        return digits
    return " ".join(text.casefold().split())


def fingerprint(kind: str, value: str, key: str | bytes | SecretStr) -> str:
    """Return the HMAC-SHA256 hex of "kind:normalised value"."""
    if not kind or ":" in kind:
        raise ValueError("kind must be non-empty and contain no colon")  # keeps the hashed input unambiguous
    normalised = _normalise(kind, value)
    if not normalised:
        # Two blank values would otherwise share a fingerprint and pass a bank-detail match.
        raise ValueError(f"nothing to fingerprint for kind {kind!r}")
    secret = key.get_secret_value() if isinstance(key, SecretStr) else key
    key_bytes = secret.encode() if isinstance(secret, str) else secret
    if not key_bytes:
        raise ValueError("fingerprint key must not be empty")
    return hmac.new(key_bytes, f"{kind}:{normalised}".encode(), hashlib.sha256).hexdigest()
