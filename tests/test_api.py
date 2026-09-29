"""CSV parsing and hourly aggregation."""

from datetime import UTC, datetime

import pytest

from custom_components.linznetz.api import LinzNetzError, complete_hours, parse_quarter_hours

from .conftest import portal_csv


def test_partial_hour_is_left_out() -> None:
    csv = portal_csv(datetime(2026, 9, 16, 0, 0), [0.1, 0.2, 0.3, 0.4, 0.5])

    assert complete_hours(parse_quarter_hours(csv)) == [
        (datetime(2026, 9, 15, 22, 0, tzinfo=UTC), 1.0)
    ]


def test_repeated_hour_in_october_is_second_utc_hour() -> None:
    header = "Datum von;Datum bis;Energiemenge in kWh;Ersatzwert"
    rows = [
        f"25.10.2026 {h}:{m};25.10.2026 00:00;{kwh};"
        for h, kwh in (("01", "0,1"), ("02", "0,2"), ("02", "0,3"), ("03", "0,4"))
        for m in ("00", "15", "30", "45")
    ]

    hours = complete_hours(parse_quarter_hours("\n".join([header, *rows])))

    assert hours == [
        (datetime(2026, 10, 24, 23, 0, tzinfo=UTC), 0.4),
        (datetime(2026, 10, 25, 0, 0, tzinfo=UTC), 0.8),
        (datetime(2026, 10, 25, 1, 0, tzinfo=UTC), 1.2),
        (datetime(2026, 10, 25, 2, 0, tzinfo=UTC), 1.6),
    ]


def test_changed_columns_are_a_portal_change() -> None:
    csv = "Von;Bis;Leistung in kW\n16.09.2026 00:00;16.09.2026 00:15;0,4\n"

    with pytest.raises(LinzNetzError) as err:
        parse_quarter_hours(csv)

    assert err.value.code == "csv-format"
