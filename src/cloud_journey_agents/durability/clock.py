"""One timezone-aware UTC clock for durable timestamps and lease checks.

Checkpoint models and storage use this helper so execution times and
lease comparisons share the same timezone convention. It reads the
current time when called and keeps no clock or database state of its own.
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
