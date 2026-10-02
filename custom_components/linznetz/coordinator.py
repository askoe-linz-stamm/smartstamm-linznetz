"""Periodic import of portal values into Home Assistant long-term statistics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .api import (
    LinzNetzAuthError,
    LinzNetzClient,
    LinzNetzError,
    complete_hours,
    parse_quarter_hours,
)
from .const import (
    CONF_PRICE_ENTITY,
    CONSUMPTION_STATISTIC,
    COST_STATISTIC,
    DOMAIN,
    HISTORY_DAYS,
    REFETCH_DAYS,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type LinzNetzConfigEntry = ConfigEntry[LinzNetzCoordinator]

_CONSUMPTION_META = StatisticMetaData(
    mean_type=StatisticMeanType.NONE,
    has_sum=True,
    name="Linz Netz Stromverbrauch",
    source=DOMAIN,
    statistic_id=CONSUMPTION_STATISTIC,
    unit_class=EnergyConverter.UNIT_CLASS,
    unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
)
_COST_META = StatisticMetaData(
    mean_type=StatisticMeanType.NONE,
    has_sum=True,
    name="Linz Netz Energiekosten",
    source=DOMAIN,
    statistic_id=COST_STATISTIC,
    unit_class=None,
    unit_of_measurement="EUR",
)


@dataclass(frozen=True)
class Problem:
    """Consecutive failed runs with the same error code, shown by the status sensor."""

    kind: str
    code: str
    since: datetime
    failed_runs: int


class LinzNetzCoordinator(DataUpdateCoordinator[datetime | None]):
    """Imports hourly consumption and returns the end of the newest imported hour.

    Every run reloads the last ``REFETCH_DAYS`` so that late or corrected portal
    values replace earlier ones. The first run loads the full portal history.
    ``problem`` describes the current failure streak, or is None after success.
    """

    config_entry: LinzNetzConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: LinzNetzConfigEntry, client: LinzNetzClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self._client = client
        self.problem: Problem | None = None
        self.last_successful_fetch: datetime | None = None
        self.next_fetch: datetime | None = None

    @callback
    def _schedule_refresh(self) -> None:
        """Expose the planned polling time, within the coordinator's subsecond jitter."""
        if self.problem is not None and self.problem.kind == "login_rejected":
            self._async_unsub_refresh()
            return
        super()._schedule_refresh()
        interval = self.update_interval
        self.next_fetch = (
            dt_util.utcnow() + interval
            if self._unsub_refresh is not None and interval is not None
            else None
        )

    @callback
    def _async_unsub_refresh(self) -> None:
        """Clear the planned time when polling is cancelled or a fetch begins."""
        super()._async_unsub_refresh()
        self.next_fetch = None

    @callback
    def _async_refresh_finished(self) -> None:
        """Publish the new schedule and error count even on repeated failures."""
        if not self.last_update_success:
            self.async_update_listeners()

    async def _async_update_data(self) -> datetime | None:
        try:
            newest = await self._async_fetch_and_import()
        except LinzNetzError as err:
            previous = self.problem
            streak = previous is not None and previous.code == err.code
            self.problem = Problem(
                kind=err.kind,
                code=err.code,
                since=previous.since if previous and streak else dt_util.utcnow(),
                failed_runs=previous.failed_runs + 1 if previous and streak else 1,
            )
            if isinstance(err, LinzNetzAuthError):
                raise ConfigEntryAuthFailed(str(err)) from err
            raise UpdateFailed(str(err)) from err
        self.problem = None
        self.last_successful_fetch = dt_util.utcnow()
        return newest

    async def _async_fetch_and_import(self) -> datetime | None:
        recorder = get_instance(self.hass)
        last = await recorder.async_add_executor_job(
            get_last_statistics, self.hass, 1, CONSUMPTION_STATISTIC, False, {"sum"}
        )
        today = dt_util.now().date()
        if rows := last.get(CONSUMPTION_STATISTIC):
            newest = dt_util.utc_from_timestamp(rows[0]["start"])
            first_day = dt_util.as_local(newest).date() - timedelta(days=REFETCH_DAYS)
        else:
            newest = None
            first_day = today - timedelta(days=HISTORY_DAYS)

        text = await self._client.async_fetch_csv(first_day, today)
        hours = await self.hass.async_add_executor_job(
            lambda: complete_hours(parse_quarter_hours(text))
        )
        if not hours:
            return None if newest is None else newest + timedelta(hours=1)

        await self._async_import(first_day, hours)
        return hours[-1][0] + timedelta(hours=1)

    async def _async_import(
        self, first_day: date, hours: list[tuple[datetime, float]]
    ) -> None:
        window_start = dt_util.start_of_local_day(first_day)
        consumption_sum = await self._async_sum_before(CONSUMPTION_STATISTIC, window_start)
        consumption: list[StatisticData] = []
        for start, kwh in hours:
            consumption_sum += kwh
            consumption.append(StatisticData(start=start, sum=round(consumption_sum, 3)))
        async_add_external_statistics(self.hass, _CONSUMPTION_META, consumption)

        price = self._price()
        if price is None:
            return
        cost_sum = await self._async_sum_before(COST_STATISTIC, window_start)
        cost: list[StatisticData] = []
        for start, kwh in hours:
            cost_sum += kwh * price
            cost.append(StatisticData(start=start, sum=round(cost_sum, 4)))
        async_add_external_statistics(self.hass, _COST_META, cost)

    async def _async_sum_before(self, statistic_id: str, moment: datetime) -> float:
        """Running total of the last hour before ``moment``, 0 before the first import."""
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            moment - timedelta(days=30),
            moment,
            {statistic_id},
            "hour",
            None,
            {"sum"},
        )
        values = rows.get(statistic_id)
        return (values[-1].get("sum") or 0.0) if values else 0.0

    def _price(self) -> float | None:
        """Energy price in EUR/kWh from the configured entity, if usable."""
        entity_id = self.config_entry.options.get(CONF_PRICE_ENTITY)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        try:
            return float(state.state) if state is not None else None
        except ValueError:
            _LOGGER.warning("Preis aus %s ist keine Zahl, Kosten übersprungen", entity_id)
            return None
