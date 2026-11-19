"""SQLite persistence: connection, migrations, repositories. All SQL lives in this package."""

from coach.db.connection import connect
from coach.db.migrate import migrate

__all__ = ["connect", "migrate"]
