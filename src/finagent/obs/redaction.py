"""Masks personal and payment data before logging, storage and prompt assembly (decision log XIII)."""

import re
from typing import Any

MASK = "[REDACTED]"

_ACCOUNT_KEYS = {"account", "account_no", "account_number", "bank_account"}
_SECRET_KEYS = {
    "abn",
    "anthropic_api_key",
    "api_key",
    "authorization",
    "bsb",
    "email",
    "hmac_key",
    "phone",
    "phone_number",
    "tfn",
    "token",
}

# A number is left alone when it is part of an ID, a decimal amount or a longer word.
_START = r"(?<![\w.,#*-])"
_END = r"(?!\w|[.,]\d)"

_API_KEY = re.compile(r"sk-ant-[\w-]+")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_BSB_AFTER_LABEL = re.compile(
    r"(?i)(\bBSB\b(?:\s+(?:number|no\.?))?\s*(?:is\b|[:#=])?\s*)\d{3}[- ]?\d{3}(?!\d)"
)
_BSB_BEFORE_LABEL = re.compile(rf"(?i){_START}\d{{3}}[- ]?\d{{3}}(\s+BSB\b)")
_BSB_ACCOUNT_PAIR = re.compile(rf"{_START}\d{{3}}-\d{{3}}[\s,;/]+(\d{{6,10}}){_END}")
_ACCOUNT_AFTER_BSB_MASK = re.compile(rf"({re.escape(MASK)}[\s,;/]+)(\d{{6,10}}){_END}")
_ABN = re.compile(r"(?i)(\bABN\b\s*[:#=]?\s*)\d{2}[ -]?\d{3}[ -]?\d{3}[ -]?\d{3}(?!\d)")
_TFN = re.compile(r"(?i)(\b(?:TFN|tax file (?:number|no\.?))\s*[:#=]?\s*)\d{3}[ -]?\d{3}[ -]?\d{2,3}(?!\d)")
_LABELLED_ACCOUNT = re.compile(
    r"(?i)((?:\bbank\s+)?(?:\bacc(?:ount)?(?:\s+(?:number|no\.?|num))?|\bacct(?:\s*(?:no\.?|number))?"
    r"|\ba/c(?:\s*no\.?)?)\s*(?:is\b|[:#=])?\s*)"
    r"(?!\d{4}-\d{2}-\d{2})(\d(?:[ -]?\d){5,15})(?!\d|[.,]\d)"
)
_PHONE = re.compile(
    rf"{_START}(?:(?:\+61|61|0)[ -]?\(?[2-478]\)?(?:[ -]?\d){{8}}"
    r"|\(0[2378]\)[ -]?(?:\d[ -]?){7}\d"
    rf"|1[38]00[ -]?\d{{3}}[ -]?\d{{3}}){_END}"
)
_SPACED_ABN = re.compile(rf"{_START}\d{{2}} \d{{3}} \d{{3}} \d{{3}}{_END}")
_SPACED_TFN = re.compile(rf"{_START}\d{{3}} \d{{3}} \d{{3}}{_END}")
_LONG_NUMBER = re.compile(rf"{_START}\d{{9,19}}{_END}")


def _last_four(text: str) -> str:
    return "****" + re.sub(r"\D", "", text)[-4:]


def _redact_text(text: str) -> str:
    # Order matters: labelled patterns run first so a generic number rule cannot consume them.
    text = _API_KEY.sub(MASK, text)
    text = _BEARER.sub(rf"\g<1>{MASK}", text)
    text = _EMAIL.sub(MASK, text)
    text = _BSB_AFTER_LABEL.sub(rf"\g<1>{MASK}", text)
    text = _BSB_BEFORE_LABEL.sub(rf"{MASK}\g<1>", text)
    text = _BSB_ACCOUNT_PAIR.sub(lambda m: f"{MASK} {_last_four(m.group(1))}", text)
    text = _ACCOUNT_AFTER_BSB_MASK.sub(lambda m: m.group(1) + _last_four(m.group(2)), text)
    text = _ABN.sub(rf"\g<1>{MASK}", text)
    text = _TFN.sub(rf"\g<1>{MASK}", text)
    text = _LABELLED_ACCOUNT.sub(lambda m: m.group(1) + _last_four(m.group(2)), text)
    text = _PHONE.sub(MASK, text)
    text = _SPACED_ABN.sub(MASK, text)
    text = _SPACED_TFN.sub(MASK, text)
    return _LONG_NUMBER.sub(lambda m: _last_four(m.group(0)), text)


def _redact_field(key: Any, value: Any) -> Any:
    # A structured field has no label inside its value, so its key decides the mask.
    is_scalar = isinstance(value, str | int) and not isinstance(value, bool)
    name = key.casefold() if isinstance(key, str) else ""
    if is_scalar and name in _ACCOUNT_KEYS:
        return _last_four(str(value))
    if is_scalar and name in _SECRET_KEYS:
        return MASK
    return redact(value)


def redact(value: Any) -> Any:
    """Return value with the same shape and sensitive data masked. Safe to apply twice."""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {key: _redact_field(key, item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    return value
