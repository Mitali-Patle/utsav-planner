r"""Normalization for user-entered text.

Indian scripts rely on the zero-width joiner (U+200D) and non-joiner (U+200C) for
conjuncts and half-forms, so those are preserved. Only control characters and the
bidirectional overrides that can disguise text are removed.
"""

import re
import unicodedata

_BIDI_CONTROLS = re.compile("[\u202a-\u202e\u2066-\u2069]")
_WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    r"""Normalize user text for storage and display.

    Applies NFKC normalization, removes bidirectional override characters, replaces
    control characters with spaces, collapses whitespace and trims the ends.

    Args:
        value: Raw text from a user or form.

    Returns:
        The normalized text.

    Examples:
        >>> normalize_text("  Priya's   Haldi\n")
        "Priya's Haldi"
        >>> normalize_text("abc\u202edef")
        'abcdef'
        >>> normalize_text("क्\u200dष") == "क्\u200dष"
        True
    """
    text = unicodedata.normalize("NFKC", value)
    text = _BIDI_CONTROLS.sub("", text)
    text = "".join(" " if unicodedata.category(char) == "Cc" else char for char in text)
    return _WHITESPACE.sub(" ", text).strip()
