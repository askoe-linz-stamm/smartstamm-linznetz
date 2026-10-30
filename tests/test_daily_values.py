"""Accounts without quarter-hour values yet: daily values and an empty portal."""

from datetime import UTC, date, datetime
import logging
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.linznetz.api import parse_daily_values
from custom_components.linznetz.const import (
    CONF_FIXED_COST_ENTITY,
    CONSUMPTION_STATISTIC,
    COST_STATISTIC,
    DOMAIN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .conftest import portal_csv
from .test_init import _sums
from .test_portal_diagnostics import EXPORT, VIEW_STATE, client_with_responses, response

# Markup as served by the portal (2026-10-30) for an account that has not
# enabled quarter-hour values yet: the evaluation kind offers only daily values.
KIND = "myForm1:j_idt1307:grid_eval:selectedClass"
DAILY_ONLY_PAGE = VIEW_STATE + f'<input type="radio" checked="checked" name="{KIND}" value="ConsumDaily" />'
SWITCHED = "<partial-response><changes><update>PRIVATE_FORM</update></changes></partial-response>"
SHOWN = f"<partial-response><update><![CDATA[{EXPORT}]]></update></partial-response>"
NO_DATA = (
    '<partial-response><changes><update id="myForm1:list"><![CDATA[<li class="netz-notification '
    'netz-notification--error"> Die Verbrauchswerte können leider nicht angezeigt werden! '
    "Entweder haben Sie einen Abfragezeitraum ohne verfügbare Verbrauchswerte gewählt oder Sie "
    "haben sich erst heute für die Verbrauchsdateninformation registriert.</li>]]></update>"
    "</changes></partial-response>"
)
DAILY_CSV = "Datum;Energiemenge in kWh;Ersatzwert\r\n24.10.2026;7,3;\r\n25.10.2026;6,5;\r\n26.10.2026;;\r\n"


async def test_daily_only_portal_exports_daily_values() -> None:
    exported = response(DAILY_CSV, content_type="text/csv")
    exported.headers = {"Content-Disposition": 'attachment; filename="export.csv"'}
    client, session = client_with_responses(
        response(DAILY_ONLY_PAGE), response(SWITCHED), response(SHOWN), exported
    )

    assert await client.async_fetch_csv(date(2026, 10, 24), date(2026, 10, 26)) == DAILY_CSV
    show_fields = session.post.call_args_list[1].kwargs["data"]
    assert show_fields[KIND] == "ConsumDaily"
    assert not any(value == "KWH" for value in show_fields.values())


async def test_portal_without_values_is_an_empty_export(caplog: pytest.LogCaptureFixture) -> None:
    page = DAILY_ONLY_PAGE.replace('value="ConsumDaily" />', 'value="ConsumDaily" /><input name="'
                                   f'{KIND}" value="ConsumQuarter" />')
    switched = '<partial-response><![CDATA[<input name="myForm1:u:selectedClass" value="KWH">]]></partial-response>'
    client, _ = client_with_responses(response(page), response(switched), response(NO_DATA))

    assert await client.async_fetch_csv(date(2026, 10, 29), date(2026, 10, 30)) == ""
    assert not caplog.records


def test_daily_values_cover_the_whole_local_day() -> None:
    # 25.10.2026 has 25 hours: clocks go back from 03:00 to 02:00.
    assert parse_daily_values(DAILY_CSV) == [
        (datetime(2026, 10, 23, 22, tzinfo=UTC), 7.3, 24),
        (datetime(2026, 10, 24, 22, tzinfo=UTC), 6.5, 25),
    ]


async def test_daily_values_then_quarter_hours(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await hass.config.async_set_time_zone("Europe/Vienna")
    freezer.move_to("2026-10-27 12:00:00+01:00")
    hass.states.async_set("input_number.fixed", "8760")  # 1 EUR per hour in 2026
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "flo", "password": "secret"},
        options={CONF_FIXED_COST_ENTITY: "input_number.fixed"},
    )
    entry.add_to_hass(hass)
    issue = ("linznetz", f"daily_only_{entry.entry_id}")
    fetch = AsyncMock(return_value=DAILY_CSV)

    with patch("custom_components.linznetz.api.LinzNetzClient.async_fetch_csv", fetch):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        # Each day in its first hour; the cost carries the fixed share of all its hours.
        assert await _sums(hass, CONSUMPTION_STATISTIC) == [7.3, 13.8]
        assert await _sums(hass, COST_STATISTIC) == [24.0, 49.0]
        assert hass.states.get("sensor.linz_netz_stromzahler_status").state == "daily_only"
        assert hass.states.get("sensor.linz_netz_stromzahler_data_until").state == (
            "2026-10-25T23:00:00+00:00"
        )
        assert ir.async_get(hass).async_get_issue(*issue) is not None

        # Quarter hours enabled: the next run replaces the newest day hour by hour.
        fetch.return_value = portal_csv(datetime(2026, 10, 25, 0, 0), [0.25] * 8)
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    assert await _sums(hass, CONSUMPTION_STATISTIC) == [7.3, 8.3, 9.3]
    assert hass.states.get("sensor.linz_netz_stromzahler_status").state == "ok"
    assert ir.async_get(hass).async_get_issue(*issue) is None


async def test_no_values_yet_is_a_notice(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"username": "flo", "password": "secret"})
    entry.add_to_hass(hass)

    with (
        caplog.at_level(logging.WARNING),
        patch(
            "custom_components.linznetz.api.LinzNetzClient.async_fetch_csv",
            AsyncMock(return_value=""),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get("sensor.linz_netz_stromzahler_status").state == "no_data"
    assert ir.async_get(hass).async_get_issue("linznetz", f"no_data_{entry.entry_id}")
    assert not [r for r in caplog.records if r.name.startswith("custom_components.linznetz")]
