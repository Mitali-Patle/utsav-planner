"""Vendored third-party files match the checksums recorded in VENDOR.md."""

import hashlib
import re
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parents[2] / "public" / "static" / "vendor"


def test_htmx_matches_recorded_checksum() -> None:
    notes = (VENDOR_DIR / "VENDOR.md").read_text(encoding="utf-8")
    match = re.search(r"SHA-256\):\*\*\s*`([0-9a-f]{64})`", notes)
    assert match, "VENDOR.md must record the htmx SHA-256"
    actual = hashlib.sha256((VENDOR_DIR / "htmx.min.js").read_bytes()).hexdigest()
    assert actual == match.group(1)
