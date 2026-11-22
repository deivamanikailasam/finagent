from decimal import Decimal

import pytest

from finagent.obs.redaction import MASK, redact

FAKE_API_KEY = "sk-" + "ant-" + "placeholder-value-123"
FAKE_BEARER = "placeholderToken123"


@pytest.mark.parametrize(
    ("text", "raw"),
    [
        ("BSB 062-000", "062-000"),
        ("BSB: 062000", "062000"),
        ("062-000 BSB", "062-000"),
        ("ABN 51 824 753 556", "824 753"),
        ("ABN: 51824753556", "51824753556"),
        ("TFN 123 456 782", "123 456"),
        ("tax file number 123456782", "123456782"),
        ("call 0491 570 156", "570"),
        ("call +61 491 570 156", "570"),
        ("call (02) 5550 1234", "5550"),
        ("email ap.clerk@example.com", "ap.clerk"),
        (f"key {FAKE_API_KEY}", FAKE_API_KEY),
        (f"Authorization: Bearer {FAKE_BEARER}", FAKE_BEARER),
    ],
)
def test_sensitive_values_are_masked(text, raw):
    result = redact(text)
    assert raw not in result
    assert MASK in result


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("account 12345678", "account ****5678"),
        ("Account Number: 12-345-678", "Account Number: ****5678"),
        ("A/C no. 987654321", "A/C no. ****4321"),
        ("bank account 12 3456 78", "bank account ****5678"),
        ("remit to 123456789012", "remit to ****9012"),
        ("Acc: 12345678", "Acc: ****5678"),
        ("Acc No 12345678", "Acc No ****5678"),
        ("new account is 12345678", "new account is ****5678"),
        ("account number is 12345678", "account number is ****5678"),
        ("BSB 062-000 Acc 12345678", f"BSB {MASK} Acc ****5678"),
        ("BSB: 062-000, 12345678", f"BSB: {MASK}, ****5678"),
        ("BSB is 062000 12345678", f"BSB is {MASK} ****5678"),
        ("bank details 062-000 12345678", f"bank details {MASK} ****5678"),
    ],
)
def test_account_numbers_keep_last_four(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "9,900.00",
        "gross 1,234,567.89",
        "1234567890.00",
        "INV-1001",
        "PO-4471",
        "FIN-POL-003#s2",
        "FIN-001",
        "2026-09-15",
        "2026-07-31T15:00:00.123456+00:00",
        "40 x 150.00",
        "L2 100 x 30.00",
        "account 9,900.00",
        "account 2026-09-15",
        "a" * 12 + "123456789012" + "b" * 12,
        "****1111",
        "the bearer of this note",
    ],
)
def test_business_values_are_not_masked(text):
    assert redact(text) == text


def test_nested_shapes_are_preserved():
    value = {
        "note": "pay account 12345678",
        "items": ["INV-1001", ("ap.clerk@example.com", 3)],
        "inner": {"amount": Decimal("9900.00"), "flag": True, "missing": None},
    }
    assert redact(value) == {
        "note": "pay account ****5678",
        "items": ["INV-1001", (MASK, 3)],
        "inner": {"amount": Decimal("9900.00"), "flag": True, "missing": None},
    }


def test_structured_fields_are_masked_by_key():
    value = {
        "account_number": 12345678,
        "bsb": "062000",
        "email": "ap.clerk@example.com",
        "invoice": "INV-1001",
    }
    assert redact(value) == {"account_number": "****5678", "bsb": MASK, "email": MASK, "invoice": "INV-1001"}


def test_input_is_not_mutated():
    value = {"note": "account 12345678"}
    redact(value)
    assert value == {"note": "account 12345678"}


@pytest.mark.parametrize(
    "value",
    [
        "BSB 062-000 account 12345678 ABN 51 824 753 556 TFN 123 456 782",
        f"0491 570 156 ap.clerk@example.com {FAKE_API_KEY} Bearer {FAKE_BEARER}",
        {"account": "12345678", "items": ["remit to 123456789012", ("BSB 062000",)]},
    ],
)
def test_redaction_is_idempotent(value):
    once = redact(value)
    assert redact(once) == once


@pytest.mark.parametrize(
    ("text", "raw"),
    [
        ("| Account | 12345678 |", "12345678"),
        ("Acc. 12345678", "12345678"),
        ("Account #: 12345678", "12345678"),
        ("Account-123456789", "123456789"),
        ("account 1234​5678", "5678​"),
        ("IBAN GB82 WEST 1234 5698 7654 32", "WEST"),
        ("| BSB | 062-000 |", "062-000"),
        ("BSB - 062-000", "062-000"),
        ("BSB 062–000", "062"),
        ("BSB 062 000", "062"),
        ("51-824-753-556", "824"),
        ("ABN 51 824 753 556", "824"),
        ("123-456-782", "456"),
        ("0491 570 156", "570"),
        ("(0491) 570 156", "570"),
        ("+61.491.570.156", "570"),
        ("+61 (0) 2 5550 1234", "5550"),
        ("tel.0491570156", "0491570156"),
        ("Bearer:placeholderToken123", "placeholderToken123"),
        ("Authorization: Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("api_" + "key=" + "placeholder12345678", "placeholder12345678"),
        ("SK-ANT-placeholder-123", "placeholder"),
    ],
)
def test_common_formats_are_masked(text, raw):
    result = redact(text)
    assert raw not in result
    assert redact(result) == result


@pytest.mark.parametrize(
    ("value", "raw"),
    [
        ({"ap.clerk@example.com": "x"}, "ap.clerk"),
        ({"account": ["12345678"]}, "12345678"),
        ({"account": Decimal("12345678")}, "12345678"),
        ({"bank_account_number": "12345678"}, "12345678"),
    ],
)
def test_keys_and_nested_sensitive_values_are_masked(value, raw):
    assert raw not in str(redact(value))


def test_text_without_sensitive_data_keeps_its_characters():
    assert redact("INV‐1001") == "INV‐1001"


def test_account_after_a_mask_is_masked_on_the_first_pass():
    assert redact("0491570156 12345678") == f"{MASK} ****5678"


def test_redaction_is_idempotent_on_generated_strings():
    import random

    rng = random.Random(20260915)
    parts = [
        "0491570156",
        "12345678",
        "BSB 062-000",
        "account",
        "ABN 51 824 753 556",
        "9,900.00",
        "INV-1001",
        "123 456 782",
        "ap.clerk@example.com",
        "Bearer placeholderToken123",
        "2026-09-15",
        "|",
        ":",
        "40 x 150.00",
    ]
    for _ in range(3000):
        text = " ".join(rng.choices(parts, k=rng.randint(1, 6)))
        once = redact(text)
        assert redact(once) == once, text
