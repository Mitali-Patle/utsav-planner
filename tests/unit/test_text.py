"""Text normalization: tidy input without corrupting Indian scripts."""

import unicodedata

import pytest

from app.domain.text import normalize_text

ZWJ = chr(0x200D)
ZWNJ = chr(0x200C)
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)
LEFT_TO_RIGHT_ISOLATE = chr(0x2066)
POP_DIRECTIONAL_ISOLATE = chr(0x2069)
FULL_WIDTH_SAMPLE = "".join(chr(ord(char) + 0xFEE0) for char in "FULLWIDTH12")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Mehendi   night  ", "Mehendi night"),
        ("line one\nline two\ttab", "line one line two tab"),
        ("bell" + chr(7) + "char", "bell char"),
        (FULL_WIDTH_SAMPLE, "FULLWIDTH12"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalizes_whitespace_controls_and_width(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_removes_bidirectional_overrides() -> None:
    disguised = f"invoice{RIGHT_TO_LEFT_OVERRIDE}fdp.exe"
    assert normalize_text(disguised) == "invoicefdp.exe"
    isolated = f"a{LEFT_TO_RIGHT_ISOLATE}b{POP_DIRECTIONAL_ISOLATE}c"
    assert normalize_text(isolated) == "abc"


@pytest.mark.parametrize(
    "text",
    [
        "क्" + ZWJ + "ष",  # Devanagari half-form with zero-width joiner
        "र्" + ZWNJ + "य",  # Devanagari with zero-width non-joiner
        "ന്" + ZWJ,  # Malayalam chillu formed with a joiner
        "ஸ்ரீ கணேஷ் சதுர்த்தி",  # Tamil
        "प्रिया की हल्दी",  # Hindi
        "शादी 🎉",  # emoji
    ],
)
def test_preserves_indic_scripts_and_joiners(text: str) -> None:
    result = normalize_text(text)
    assert result == unicodedata.normalize("NFKC", text)
    assert result.count(ZWJ) == text.count(ZWJ)
    assert result.count(ZWNJ) == text.count(ZWNJ)
