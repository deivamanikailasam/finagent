"""Masks personal and payment data before logging, storage and prompt assembly (decision log XIII)."""

import re
import unicodedata
from typing import Any

MASK = "[REDACTED]"

_ACCOUNT_TOKENS = {"account", "acct"}
_NOT_ACCOUNT_VALUE_TOKENS = {"count", "name", "status", "type"}
_SECRET_TOKENS = {"abn", "authorization", "bsb", "email", "password", "phone", "secret", "tfn", "token"}
_SECRET_NAMES = ("api_key", "hmac_key")

# A number is left alone when it is part of an ID, a decimal amount or a longer word.
_START = r"(?<![\w.,#*-])"
_END = r"(?!\w|[.,]\d)"
_SEP = r"(?:\s*\bis\b)?[\s:#=|.\-]*"

_AUTHORIZATION = re.compile(r"(?i)(\bauthorization\s*[:=]\s*)(?:(?:bearer|basic)\s+)?\S+")
_CREDENTIAL = re.compile(
    r"""(?i)\b((?:api[_-]?key|access[_-]?token|secret|password|passwd)\s*[:=]\s*)["']?[^\s"',;]+"""
)
_API_KEY = re.compile(r"(?i)sk-ant-[\w-]+")
_BEARER = re.compile(r"(?i)\b(bearer[\s:=]+)[A-Za-z0-9._~+/=-]{8,}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
_BSB_AFTER_LABEL = re.compile(rf"(?i)(\bBSB\b(?:\s+(?:number|no\.?))?{_SEP})\d{{3}}[- ]?\d{{3}}(?!\d)")
_BSB_BEFORE_LABEL = re.compile(rf"(?i){_START}\d{{3}}[- ]?\d{{3}}(\s+BSB\b)")
_BSB_ACCOUNT_PAIR = re.compile(rf"{_START}\d{{3}}-\d{{3}}[\s,;/]+(\d{{6,10}}){_END}")
_ABN = re.compile(rf"(?i)(\bABN\b{_SEP})\d{{2}}[ -]?\d{{3}}[ -]?\d{{3}}[ -]?\d{{3}}(?!\d)")
_TFN = re.compile(
    rf"(?i)(\b(?:TFN|tax file (?:number|no\.?))\b{_SEP})\d{{3}}[ -]?\d{{3}}[ -]?\d{{2,3}}(?!\d)"
)
_LABELLED_ACCOUNT = re.compile(
    r"(?i)((?:\bbank\s+)?(?:\bacc(?:ount|t)?\.?(?:\s*(?:number|num|no)\.?)?|\ba/c(?:\s*no\.?)?)"
    + _SEP
    + r")(?!\d{4}-\d{2}-\d{2})(?!\d{2}-\d{2}-\d{4})(\d(?:[ -]?\d){5,15})(?!\w|[.,]\d)"
)
_PHONE = re.compile(
    r"(?<![\w#*-])(?:(?:\+?61[ .-]?(?:\(0\)[ .-]?)?|\(?0)[2-478](?:[ .\-)]{0,2}\d){8}"
    r"|\(0[2378]\)[ -]?(?:\d[ -]?){7}\d"
    rf"|1[38]00[ -]?\d{{3}}[ -]?\d{{3}}){_END}"
)
_GROUPED_ABN = re.compile(rf"{_START}\d{{2}}[ -]\d{{3}}[ -]\d{{3}}[ -]\d{{3}}{_END}")
_GROUPED_TFN = re.compile(rf"{_START}\d{{3}}[ -]\d{{3}}[ -]\d{{3}}{_END}")
_ACCOUNT_AFTER_MASK = re.compile(rf"({re.escape(MASK)}[\s,;/]+)(\d{{6,10}}){_END}")
_LONG_NUMBER = re.compile(rf"{_START}\d{{9,19}}{_END}")


def _last_four(text: str) -> str:
    return "****" + re.sub(r"\D", "", text)[-4:]


def _matching_view(text: str) -> str:
    """Normalise look-alike digits, spaces and dashes so they cannot hide a value from the patterns."""
    characters = []
    for character in unicodedata.normalize("NFKC", text):
        category = unicodedata.category(character)
        if category == "Cf":
            continue
        if category == "Zs":
            characters.append(" ")
        elif category == "Pd":
            characters.append("-")
        else:
            characters.append(character)
    return "".join(characters)


def _apply_patterns(text: str) -> str:
    # Order matters: labelled patterns run first so a generic number rule cannot consume them.
    text = _AUTHORIZATION.sub(rf"\g<1>{MASK}", text)
    text = _CREDENTIAL.sub(rf"\g<1>{MASK}", text)
    text = _API_KEY.sub(MASK, text)
    text = _BEARER.sub(rf"\g<1>{MASK}", text)
    text = _EMAIL.sub(MASK, text)
    text = _IBAN.sub(MASK, text)
    text = _BSB_AFTER_LABEL.sub(rf"\g<1>{MASK}", text)
    text = _BSB_BEFORE_LABEL.sub(rf"{MASK}\g<1>", text)
    text = _BSB_ACCOUNT_PAIR.sub(lambda m: f"{MASK} {_last_four(m.group(1))}", text)
    text = _ABN.sub(rf"\g<1>{MASK}", text)
    text = _TFN.sub(rf"\g<1>{MASK}", text)
    text = _LABELLED_ACCOUNT.sub(lambda m: m.group(1) + _last_four(m.group(2)), text)
    text = _PHONE.sub(MASK, text)
    text = _GROUPED_ABN.sub(MASK, text)
    text = _GROUPED_TFN.sub(MASK, text)
    text = _ACCOUNT_AFTER_MASK.sub(lambda m: m.group(1) + _last_four(m.group(2)), text)
    return _LONG_NUMBER.sub(lambda m: _last_four(m.group(0)), text)


def _redact_text(text: str) -> str:
    view = _matching_view(text)
    masked = view
    for _ in range(4):  # a mask can expose a neighbouring value, so repeat until stable
        next_pass = _apply_patterns(masked)
        if next_pass == masked:
            break
        masked = next_pass
    return text if masked == view else masked  # text with nothing to mask keeps its original characters


def _key_kind(key: Any) -> str | None:
    if not isinstance(key, str):
        return None
    name = key.casefold()
    tokens = set(re.split(r"[^a-z0-9]+", name))
    if tokens & _NOT_ACCOUNT_VALUE_TOKENS:
        return None
    if tokens & _ACCOUNT_TOKENS:
        return "account"
    if tokens & _SECRET_TOKENS or any(secret in name for secret in _SECRET_NAMES):
        return "secret"
    return None


def _mask_value(value: Any, kind: str) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return {_redact_key(key): _mask_value(item, kind) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_value(item, kind) for item in value]
    if isinstance(value, tuple):
        return tuple(_mask_value(item, kind) for item in value)
    return _last_four(str(value)) if kind == "account" else MASK


def _redact_key(key: Any) -> Any:
    return _redact_text(key) if isinstance(key, str) else key


def _redact_field(key: Any, value: Any) -> Any:
    # A structured field has no label inside its value, so its key decides the mask.
    kind = _key_kind(key)
    return _mask_value(value, kind) if kind else redact(value)


def redact(value: Any) -> Any:
    """Return value with the same shape and sensitive data masked. Safe to apply twice."""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {_redact_key(key): _redact_field(key, item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    return value
