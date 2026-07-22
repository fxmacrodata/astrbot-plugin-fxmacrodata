"""Parsing and validation helpers for opt-in AstrBot macro notifications."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_CALENDAR_ROW = re.compile(
    r"^\|\s*(?P<when>\d{4}-\d{2}-\d{2}T[^|]+?)\s*\|\s*"
    r"(?P<release>[^|]+?)\s*\|.*?\|\s*(?P<confirmed>Yes|No)\s*\|$",
    re.IGNORECASE,
)
_CURRENCY = re.compile(r"^[A-Za-z]{3}$")
_INDICATOR = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_TIME = re.compile(r"^(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)$")
_WEEKDAY = {"sun", "mon", "tue", "wed", "thu", "fri", "sat"}


@dataclass(frozen=True)
class ReleaseEvent:
    """One exact, official-confirmed release time returned by the hosted MCP."""

    when: datetime
    release_name: str

    @property
    def key(self) -> str:
        return f"{self.when.isoformat()}|{self.release_name}"


def parse_confirmed_release_events(markdown: str) -> tuple[ReleaseEvent, ...]:
    """Parse confirmed calendar table rows without inferring any schedule."""

    events: list[ReleaseEvent] = []
    for line in markdown.splitlines():
        match = _CALENDAR_ROW.match(line.strip())
        if not match or match.group("confirmed").lower() != "yes":
            continue
        try:
            when = datetime.fromisoformat(match.group("when").strip())
        except ValueError:
            continue
        if when.tzinfo is None:
            continue
        events.append(
            ReleaseEvent(when=when, release_name=match.group("release").strip())
        )
    return tuple(events)


def next_release_event(
    markdown: str, *, now: datetime | None = None
) -> ReleaseEvent | None:
    """Return the next confirmed event shown by the hosted release calendar."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    future = [
        event
        for event in parse_confirmed_release_events(markdown)
        if event.when.astimezone(timezone.utc) >= current
    ]
    return min(future, key=lambda event: event.when) if future else None


def validate_currency(value: str) -> str:
    """Validate a three-letter currency code for command and page inputs."""

    candidate = value.strip().upper()
    if not _CURRENCY.fullmatch(candidate):
        raise ValueError("Currency must be a three-letter ISO code.")
    return candidate


def validate_indicator(value: str) -> str:
    """Validate a public indicator slug without broadening tool arguments."""

    candidate = value.strip().lower()
    if not _INDICATOR.fullmatch(candidate):
        raise ValueError(
            "Indicator must use lowercase letters, digits, and underscores."
        )
    return candidate


def validate_time(value: str) -> tuple[int, int]:
    """Validate a 24-hour local wall-clock time."""

    match = _TIME.fullmatch(value.strip())
    if not match:
        raise ValueError("Time must use 24-hour HH:MM format.")
    return int(match.group("hour")), int(match.group("minute"))


def validate_timezone(value: str) -> str:
    """Validate an IANA timezone accepted by AstrBot's cron manager."""

    candidate = value.strip()
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            "Timezone must be an IANA timezone, for example Australia/Sydney."
        ) from exc
    return candidate


def validate_weekday(value: str) -> str:
    """Validate AstrBot's named weekly cron weekday."""

    candidate = value.strip().lower()
    if candidate not in _WEEKDAY:
        raise ValueError("Weekday must be sun, mon, tue, wed, thu, fri, or sat.")
    return candidate
