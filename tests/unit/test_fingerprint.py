import hashlib
import hmac

import pytest
from pydantic import SecretStr

from finagent.obs.fingerprint import fingerprint

KEY = "0" * 64
OTHER_KEY = "1" * 64


def test_matches_hmac_over_kind_and_normalised_value():
    expected = hmac.new(KEY.encode(), b"account:12345678", hashlib.sha256).hexdigest()
    assert fingerprint("account", "1234-5678", KEY) == expected


def test_stable_across_account_formatting():
    assert fingerprint("account", "12 345 678", KEY) == fingerprint("account", "12345678", KEY)


def test_key_dependent():
    assert fingerprint("account", "12345678", KEY) != fingerprint("account", "12345678", OTHER_KEY)


def test_kind_dependent():
    assert fingerprint("account", "12345678", KEY) != fingerprint("bsb", "12345678", KEY)


def test_accepts_secret_key():
    assert fingerprint("account", "12345678", SecretStr(KEY)) == fingerprint("account", "12345678", KEY)


def test_output_does_not_contain_raw_value():
    assert "12345678" not in fingerprint("account", "12345678", KEY)


@pytest.mark.parametrize("value", ["", "   ", "no digits"])
def test_empty_account_cannot_match_another_empty_account(value):
    with pytest.raises(ValueError):
        fingerprint("account", value, KEY)


def test_non_ascii_digits_are_rejected():
    with pytest.raises(ValueError):
        fingerprint("account", "12٣٤4", KEY)


def test_kind_cannot_contain_the_separator():
    with pytest.raises(ValueError):
        fingerprint("a:b", "c", KEY)


@pytest.mark.parametrize("empty", ["", b"", SecretStr("")])
def test_empty_key_is_rejected(empty):
    with pytest.raises(ValueError):
        fingerprint("account", "12345678", empty)
