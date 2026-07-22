from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alerts import (
    next_release_event,
    parse_confirmed_release_events,
    validate_currency,
    validate_indicator,
    validate_time,
    validate_timezone,
    validate_weekday,
)


CALENDAR = """## USD Release Calendar

| Time | Release | Source | Confirmed |
|---|---|---|---|
| 2026-07-23T08:30:00-04:00 | Initial Jobless Claims | Official source | Yes |
| 2026-07-24T08:30:00-04:00 | Test release | Official source | No |
"""


def test_calendar_parser_uses_only_exact_confirmed_rows():
    events = parse_confirmed_release_events(CALENDAR)

    assert len(events) == 1
    assert events[0].release_name == "Initial Jobless Claims"
    assert events[0].when.isoformat() == "2026-07-23T08:30:00-04:00"
    assert (
        next_release_event(CALENDAR, now=datetime(2026, 7, 22, tzinfo=timezone.utc))
        == events[0]
    )


@pytest.mark.parametrize(
    ("validator", "value", "expected"),
    [
        (validate_currency, " usd ", "USD"),
        (validate_indicator, "core_pce_mom", "core_pce_mom"),
        (validate_time, "08:15", (8, 15)),
        (validate_timezone, "Australia/Sydney", "Australia/Sydney"),
        (validate_weekday, "Mon", "mon"),
    ],
)
def test_alert_input_validation_normalizes_supported_values(validator, value, expected):
    assert validator(value) == expected


@pytest.mark.parametrize(
    ("validator", "value"),
    [
        (validate_currency, "US"),
        (validate_indicator, "CPI growth"),
        (validate_time, "8:15"),
        (validate_timezone, "Not/AZone"),
        (validate_weekday, "monday"),
    ],
)
def test_alert_input_validation_rejects_unsafe_or_ambiguous_values(validator, value):
    with pytest.raises(ValueError):
        validator(value)
