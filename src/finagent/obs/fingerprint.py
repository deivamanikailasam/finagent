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
        return re.sub(r"[^0-9]", "", text)
    return " ".join(text.casefold().split())


def fingerprint(kind: str, value: str, key: str | bytes | SecretStr) -> str:
    """Return the HMAC-SHA256 hex of "kind:normalised value"."""
    normalised = _normalise(kind, value)
    if not normalised:
        # Two blank values would otherwise share a fingerprint and pass a bank-detail match.
        raise ValueError(f"nothing to fingerprint for kind {kind!r}")
    secret = key.get_secret_value() if isinstance(key, SecretStr) else key
    key_bytes = secret.encode() if isinstance(secret, str) else secret
    return hmac.new(key_bytes, f"{kind}:{normalised}".encode(), hashlib.sha256).hexdigest()
