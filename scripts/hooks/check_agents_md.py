"""Fail when AGENTS.md exceeds the character budget that Antigravity rule files allow."""

import sys
from pathlib import Path

LIMIT = 10_000


def main() -> int:
    """Check the length of AGENTS.md.

    Returns:
        Process exit code: 0 when within budget, 1 otherwise.
    """
    length = len(Path("AGENTS.md").read_text(encoding="utf-8"))
    if length > LIMIT:
        print(f"Blocked: AGENTS.md has {length} characters; the limit is {LIMIT}.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
