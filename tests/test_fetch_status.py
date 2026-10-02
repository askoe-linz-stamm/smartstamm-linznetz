"""Fetch timestamps follow successful runs and the polling lifecycle."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from custom_components.linznetz.api import LinzNetzAuthError, LinzNetzConnectionError
from custom_components.linznetz.const import DOMAIN

from .conftest import portal_csv

DATA_UNTIL = "sensor.linz_netz_stromzahler_data_until"
LAST_FETCH = "sensor.linz_netz_stromzahler_last_successful_fetch"
NEXT_FETCH = "sensor.linz_netz_stromzahler_next_scheduled_fetch"
STATUS = "sensor.linz_netz_stromzahler_status"
CSV = portal_csv(datetime(2026, 9, 20), [0.25] * 4)


async def test_fetch_times_for_unchanged_data_and_repeated_failures(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-09-21 06:00:00+00:00")
    entry = MockConfigEntry(domain=DOMAIN, data={"username": "verein", "password": "secret"})
    entry.add_to_hass(hass)
    fetch = AsyncMock(return_value=CSV)

    with patch("custom_components.linznetz.api.LinzNetzClient.async_fetch_csv", fetch):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(LAST_FETCH).state == "2026-09-21T06:00:00+00:00"
        assert hass.states.get(NEXT_FETCH).state == "2026-09-21T12:00:00+00:00"
        horizon = hass.states.get(DATA_UNTIL).state

        # A successful fetch with the same readings is still a new successful check.
        freezer.move_to("2026-09-21 08:00:00+00:00")
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(LAST_FETCH).state == "2026-09-21T08:00:00+00:00"
        assert hass.states.get(NEXT_FETCH).state == "2026-09-21T14:00:00+00:00"
        assert hass.states.get(DATA_UNTIL).state == horizon

        fetch.side_effect = LinzNetzConnectionError("request", "Portal nicht erreichbar")
        for hour in (9, 10):
            freezer.move_to(f"2026-09-21 {hour:02}:00:00+00:00")
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()
            assert hass.states.get(LAST_FETCH).state == "2026-09-21T08:00:00+00:00"
            assert hass.states.get(DATA_UNTIL).state == horizon
            assert hass.states.get(NEXT_FETCH).state == (
                f"2026-09-21T{hour + 6:02}:00:00+00:00"
            )
        assert hass.states.get(STATUS).attributes["failed_runs"] == 2

        fetch.side_effect = None
        freezer.move_to("2026-09-21 11:00:00+00:00")
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(LAST_FETCH).state == "2026-09-21T11:00:00+00:00"
        assert hass.states.get(STATUS).state == "ok"
        assert "failed_runs" not in hass.states.get(STATUS).attributes


async def test_periodic_fetch_matches_the_displayed_schedule(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-09-21 06:00:00+00:00")
    entry = MockConfigEntry(domain=DOMAIN, data={"username": "verein", "password": "secret"})
    entry.add_to_hass(hass)
    fetch = AsyncMock(return_value=CSV)

    with patch("custom_components.linznetz.api.LinzNetzClient.async_fetch_csv", fetch):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        before = dt_util.utcnow()
        planned = dt_util.parse_datetime(hass.states.get(NEXT_FETCH).state)
        freezer.move_to(planned)
        # Advance UTC for the eagerly started task while the helper advances
        # timers relative to the unchanged monotonic event-loop clock.
        with patch(
            "pytest_homeassistant_custom_component.common.time.time",
            return_value=before.timestamp(),
        ):
            async_fire_time_changed(hass, planned)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert fetch.await_count == 2
        assert dt_util.parse_datetime(hass.states.get(LAST_FETCH).state) >= planned
        assert dt_util.parse_datetime(hass.states.get(NEXT_FETCH).state) > planned

    coordinator = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert coordinator.next_fetch is None


@pytest.mark.parametrize("login_rejected", [False, True])
async def test_no_planned_fetch_when_polling_is_disabled_or_login_rejected(
    hass: HomeAssistant, login_rejected: bool
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "verein", "password": "secret"},
        pref_disable_polling=not login_rejected,
    )
    entry.add_to_hass(hass)
    fetch = AsyncMock(return_value=CSV)
    if login_rejected:
        fetch.side_effect = LinzNetzAuthError("login", "Anmeldung abgelehnt")

    with patch("custom_components.linznetz.api.LinzNetzClient.async_fetch_csv", fetch):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get(NEXT_FETCH).state == "unknown"
    if login_rejected:
        assert hass.states.get(LAST_FETCH).state == "unknown"
        assert hass.states.get(STATUS).state == "login_rejected"


@pytest.mark.parametrize("first_fetch_fails", [False, True])
async def test_restore_timestamps_without_overwriting_a_new_success(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, first_fetch_fails: bool
) -> None:
    freezer.move_to("2026-09-21 06:00:00+00:00")
    previous_until = "2026-09-18T22:00:00+00:00"
    previous_fetch = "2026-09-19T06:00:00+00:00"
    mock_restore_cache(
        hass, [State(DATA_UNTIL, previous_until), State(LAST_FETCH, previous_fetch)]
    )
    entry = MockConfigEntry(domain=DOMAIN, data={"username": "verein", "password": "secret"})
    entry.add_to_hass(hass)
    fetch = AsyncMock(return_value=CSV)
    if first_fetch_fails:
        fetch.side_effect = LinzNetzConnectionError("request", "Portal nicht erreichbar")

    with patch("custom_components.linznetz.api.LinzNetzClient.async_fetch_csv", fetch):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get(DATA_UNTIL).state == (
        previous_until if first_fetch_fails else "2026-09-19T23:00:00+00:00"
    )
    assert hass.states.get(LAST_FETCH).state == (
        previous_fetch if first_fetch_fails else "2026-09-21T06:00:00+00:00"
    )


async def test_successful_empty_export_does_not_restore_old_consumption(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-09-21 06:00:00+00:00")
    mock_restore_cache(
        hass,
        [
            State(DATA_UNTIL, "2026-09-18T22:00:00+00:00"),
            State(LAST_FETCH, "2026-09-19T06:00:00+00:00"),
        ],
    )
    entry = MockConfigEntry(domain=DOMAIN, data={"username": "verein", "password": "secret"})
    entry.add_to_hass(hass)
    with patch(
        "custom_components.linznetz.api.LinzNetzClient.async_fetch_csv",
        AsyncMock(return_value=portal_csv(datetime(2026, 9, 20), [])),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get(DATA_UNTIL).state == "unknown"
    assert hass.states.get(LAST_FETCH).state == "2026-09-21T06:00:00+00:00"
    assert hass.states.get(STATUS).state == "ok"


@pytest.mark.parametrize(
    "previous_state", ["unknown", "unavailable", "2026-09-19T06:00:00"]
)
async def test_unusable_restored_timestamps_stay_unknown(
    hass: HomeAssistant, previous_state: str
) -> None:
    mock_restore_cache(
        hass, [State(DATA_UNTIL, previous_state), State(LAST_FETCH, previous_state)]
    )
    entry = MockConfigEntry(domain=DOMAIN, data={"username": "verein", "password": "secret"})
    entry.add_to_hass(hass)
    with patch(
        "custom_components.linznetz.api.LinzNetzClient.async_fetch_csv",
        AsyncMock(side_effect=LinzNetzConnectionError("request", "Portal nicht erreichbar")),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get(DATA_UNTIL).state == "unknown"
    assert hass.states.get(LAST_FETCH).state == "unknown"
