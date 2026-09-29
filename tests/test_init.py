"""Import into long-term statistics."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.linznetz.api import LinzNetzError
from custom_components.linznetz.const import (
    CONF_PRICE_ENTITY,
    CONSUMPTION_STATISTIC,
    COST_STATISTIC,
    DOMAIN,
)
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant

from .conftest import portal_csv

START = datetime(2026, 9, 20, 0, 0)  # local midnight = 22:00 UTC the day before


async def _sums(hass: HomeAssistant, statistic_id: str) -> list[float]:
    await async_wait_recording_done(hass)
    rows = statistics_during_period(
        hass, datetime(2026, 9, 1, tzinfo=UTC), None, {statistic_id}, "hour", None, {"sum"}
    )
    return [round(row["sum"], 4) for row in rows.get(statistic_id, [])]


async def test_import_continues_and_corrects_overlap(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await hass.config.async_set_time_zone("Europe/Vienna")
    freezer.move_to("2026-09-21 12:00:00+02:00")
    hass.states.async_set("input_number.price", "0.5")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "verein", "password": "secret"},
        options={CONF_PRICE_ENTITY: "input_number.price"},
    )
    entry.add_to_hass(hass)
    fetch = AsyncMock(return_value=portal_csv(START, [0.25] * 8))

    with patch(
        "custom_components.linznetz.api.LinzNetzClient.async_fetch_csv", fetch
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await _sums(hass, CONSUMPTION_STATISTIC) == [1.0, 2.0]
        assert await _sums(hass, COST_STATISTIC) == [0.5, 1.0]
        assert hass.states.get("sensor.linz_netz_stromzahler_data_until").state == (
            "2026-09-20T00:00:00+00:00"
        )

        # Next run: the portal corrected the second hour and added a third.
        fetch.return_value = portal_csv(START, [0.25] * 4 + [0.5] * 8)
        await entry.runtime_data.async_refresh()

    assert await _sums(hass, CONSUMPTION_STATISTIC) == [1.0, 3.0, 5.0]
    assert fetch.await_args_list[-1].args[0].isoformat() == "2026-09-13"


async def test_status_reports_lasting_portal_change(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await hass.config.async_set_time_zone("Europe/Vienna")
    freezer.move_to("2026-09-21 12:00:00+02:00")
    entry = MockConfigEntry(domain=DOMAIN, data={"username": "verein", "password": "secret"})
    entry.add_to_hass(hass)
    fetch = AsyncMock(side_effect=LinzNetzError("export-link", "CSV-Export nicht gefunden"))
    status = "sensor.linz_netz_stromzahler_status"
    data_until = "sensor.linz_netz_stromzahler_data_until"

    with patch("custom_components.linznetz.api.LinzNetzClient.async_fetch_csv", fetch):
        # A failing first run still sets up the entry, so the problem is visible.
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(status).state == "portal_changed"

        fetch.side_effect = None
        fetch.return_value = portal_csv(START, [0.25] * 4)
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(status).state == "ok"
        assert hass.states.get(status).attributes.get("error_code") is None

        fetch.side_effect = LinzNetzError("export-link", "CSV-Export nicht gefunden")
        await entry.runtime_data.async_refresh()
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    state = hass.states.get(status)
    assert state.state == "portal_changed"
    assert state.attributes["error_code"] == "export-link"
    assert state.attributes["failed_runs"] == 2
    # The statistics did not change, so the last import time stays visible.
    assert hass.states.get(data_until).state == "2026-09-19T23:00:00+00:00"
