"""NEM time helpers. AEMO publishes all MMS times in NEM time (AEST, UTC+10, no DST)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

NEM_TZ = timezone(timedelta(hours=10), "NEM")

_FRACTION = re.compile(r"(?<=:\d\d)\.\d+$")
_FORMATS = ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d")


def parse_nem(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip().strip('"')
    value = _FRACTION.sub("", value)  # AEMO archives write milliseconds, e.g. 2011/01/28 10:29:12.250
    for fmt in _FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=NEM_TZ)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=NEM_TZ)
    except ValueError:
        return None


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(NEM_TZ).isoformat(timespec="seconds") if dt else None


def now_nem() -> datetime:
    return datetime.now(NEM_TZ)
