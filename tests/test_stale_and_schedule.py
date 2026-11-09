"""Hourly fetches until the previous day arrives, and a notice when values stop."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import issue_registry as ir

from custom_components.linznetz.api import LinzNetzConnectionError
from custom_components.linznetz.const import DOMAIN

from .conftest import portal_csv

DATA_UNTIL = "sensor.linz_netz_stromzahler_data_until"
NEXT_FETCH = "sensor.linz_netz_stromzahler_next_scheduled_fetch"
STATUS = "sensor.linz_netz_stromzahler_status"


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    await hass.config.async_set_time_zone("Europe/Vienna")
    entry = MockConfigEntry(domain=DOMAIN, data={"username": "flo", "password": "secret"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_hourly_until_the_previous_day_is_imported(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-09 08:51:00+01:00")
    # Values up to 08.11. 00:00: the 8th is still missing.
    fetch = AsyncMock(return_value=portal_csv(datetime(2026, 11, 7), [0.25] * 96))

    with patch("custom_components.linznetz.api.LinzNetzClient.async_fetch_csv", fetch):
        entry = await _setup(hass)
        assert hass.states.get(NEXT_FETCH).state == "2026-11-09T08:51:00+00:00"

        freezer.move_to("2026-11-09 09:51:00+01:00")
        fetch.return_value = portal_csv(datetime(2026, 11, 7), [0.25] * 192)
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    assert hass.states.get(NEXT_FETCH).state == "2026-11-09T14:51:00+00:00"
    assert hass.states.get(STATUS).state == "ok"


async def test_stale_values_are_a_notice(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-09 08:51:00+01:00")
    # The portal still ends at the meter replacement on 05.11. 06:00.
    fetch = AsyncMock(return_value=portal_csv(datetime(2026, 11, 5, 5), [0.25] * 4))

    with patch("custom_components.linznetz.api.LinzNetzClient.async_fetch_csv", fetch):
        entry = await _setup(hass)
        issue = (DOMAIN, f"stale_{entry.entry_id}")
        assert hass.states.get(STATUS).state == "stale"
        assert ir.async_get(hass).async_get_issue(*issue) is not None
        assert hass.states.get(NEXT_FETCH).state == "2026-11-09T08:51:00+00:00"

        # Runs without newer values keep the notice, also an empty export or daily values.
        for hour, text in (
            (9, ""),
            (10, "Datum;Energiemenge in kWh;Ersatzwert\r\n04.11.2026;7,3;\r\n"),
        ):
            freezer.move_to(f"2026-11-09 {hour:02}:51:00+01:00")
            fetch.return_value = text
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()
            assert hass.states.get(STATUS).state == "stale"

        # Late values arrive: the notice disappears.
        freezer.move_to("2026-11-09 11:51:00+01:00")
        fetch.return_value = portal_csv(datetime(2026, 11, 5, 5), [0.25] * 4 * 91)
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    assert hass.states.get(STATUS).state == "ok"
    assert ir.async_get(hass).async_get_issue(*issue) is None


async def test_hourly_after_a_failed_first_fetch_on_restart(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-09 08:51:00+01:00")
    mock_restore_cache(hass, [State(DATA_UNTIL, "2026-11-07T23:00:00+00:00")])
    fetch = AsyncMock(side_effect=LinzNetzConnectionError("connection", "Portal nicht erreichbar"))

    with patch("custom_components.linznetz.api.LinzNetzClient.async_fetch_csv", fetch):
        await _setup(hass)

    assert hass.states.get(DATA_UNTIL).state == "2026-11-07T23:00:00+00:00"
    assert hass.states.get(NEXT_FETCH).state == "2026-11-09T08:51:00+00:00"
