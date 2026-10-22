"""Create the database tables once per environment.

Run with `uv run python -m scripts.init_db`. It uses DATABASE_URL from the environment or
`.env`; creating tables is idempotent, so running it twice is safe.
"""

import sys

from app.config import get_settings
from app.persistence.db import make_engine
from app.persistence.tables import metadata


def main() -> int:
    """Create any missing tables and report which exist."""
    settings = get_settings()
    if settings.database_url is None:
        print("DATABASE_URL is not set.")
        return 1
    engine = make_engine(settings.database_url.get_secret_value())
    metadata.create_all(engine)
    print(f"Tables ready ({settings.app_env}): {', '.join(sorted(metadata.tables))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
