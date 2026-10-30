"""Periodic import of portal values into Home Assistant long-term statistics."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math

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
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .api import (
    VIENNA,
    LinzNetzAuthError,
    LinzNetzClient,
    LinzNetzError,
    complete_hours,
    is_daily_export,
    parse_daily_values,
    parse_quarter_hours,
)
from .const import (
    CONF_FIXED_COST_ENTITY,
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


def hourly_cost(
    start: datetime, kwh: float, price: float, fixed_per_year: float, hours: int = 1
) -> float:
    """Cost in EUR of the ``hours`` starting at ``start`` (a whole day for daily values).

    Energy at ``price`` EUR/kWh plus an even share of the yearly fixed costs. The
    share depends on the Vienna calendar year of the hour (8760 or 8784 hours), so
    a full year of imported hours adds up to exactly ``fixed_per_year``.
    """
    year = start.astimezone(VIENNA).year
    hours_in_year = 8784 if calendar.isleap(year) else 8760
    return kwh * price + fixed_per_year * hours / hours_in_year


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
    ``notice`` is set after a successful run that found no values (``no_data``) or
    only daily values (``daily_only``); each day's kWh then lands in its first hour.
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
        self.notice: str | None = None
        self.last_successful_fetch: datetime | None = None
        self.next_fetch: datetime | None = None

    @callback
    def _schedule_refresh(self) -> None:
        """Expose the planned polling time, within about a second of the polling timer.

        Overrides a private DataUpdateCoordinator hook, like ``_async_unsub_refresh``
        below; the schedule and login tests guard against changes in Home Assistant.
        """
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
        periods = await self.hass.async_add_executor_job(_parse_export, text)
        notice = "no_data" if not text else "daily_only" if is_daily_export(text) else None
        self._set_notice(notice)
        if not periods:
            # Keep the known horizon: after daily values it is a day past ``newest``.
            return self.data or (None if newest is None else newest + timedelta(hours=1))

        await self._async_import(periods)
        start, _, hours = periods[-1]
        return start + timedelta(hours=hours)

    def _set_notice(self, notice: str | None) -> None:
        """Show a repair notice while the portal has no values or only daily values.

        ``None`` clears both; the integration does this when it is unloaded.
        """
        self.notice = notice
        for kind in ("no_data", "daily_only"):
            issue_id = f"{kind}_{self.config_entry.entry_id}"
            if kind == notice:
                ir.async_create_issue(
                    self.hass, DOMAIN, issue_id, is_fixable=False,
                    severity=ir.IssueSeverity.WARNING, translation_key=kind,
                )
            else:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def _async_import(self, periods: list[tuple[datetime, float, int]]) -> None:
        # Continue from the last sum before the first new row. Rows before it
        # stay untouched, also if the portal no longer reports their days.
        window_start = periods[0][0]
        consumption_sum = await self._async_sum_before(CONSUMPTION_STATISTIC, window_start)
        consumption: list[StatisticData] = []
        for start, kwh, _ in periods:
            consumption_sum += kwh
            consumption.append(StatisticData(start=start, sum=round(consumption_sum, 3)))
        async_add_external_statistics(self.hass, _CONSUMPTION_META, consumption)

        cost_inputs = self._cost_inputs()
        if cost_inputs is None:
            return
        price, fixed_per_year = cost_inputs
        cost_sum = await self._async_sum_before(COST_STATISTIC, window_start)
        cost: list[StatisticData] = []
        for start, kwh, hours in periods:
            cost_sum += hourly_cost(start, kwh, price, fixed_per_year, hours)
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

    def _cost_inputs(self) -> tuple[float, float] | None:
        """Price in EUR/kWh and fixed costs in EUR/year from the configured entities.

        An entity that is not configured counts as 0. Returns None when neither is
        configured or a configured one has no usable value: the run then skips the
        cost statistic and keeps the existing values instead of writing a partial cost.
        """
        options = self.config_entry.options
        price_entity = options.get(CONF_PRICE_ENTITY)
        fixed_entity = options.get(CONF_FIXED_COST_ENTITY)
        if not price_entity and not fixed_entity:
            return None
        price = self._number(price_entity) if price_entity else 0.0
        fixed_per_year = self._number(fixed_entity) if fixed_entity else 0.0
        if price is None or fixed_per_year is None:
            return None
        return price, fixed_per_year

    def _number(self, entity_id: str) -> float | None:
        """Finite numeric state of ``entity_id``, or None if missing or not a number.

        NaN or infinity would corrupt the running cost sum, so they count as unusable.
        """
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        try:
            value = float(state.state)
        except ValueError:
            value = math.nan
        if not math.isfinite(value):
            _LOGGER.warning("Wert aus %s ist keine Zahl, Kosten übersprungen", entity_id)
            return None
        return value


def _parse_export(text: str) -> list[tuple[datetime, float, int]]:
    """Rows to import as (UTC start, kWh, hours covered), oldest first."""
    if not text:
        return []
    if is_daily_export(text):
        return parse_daily_values(text)
    return [(start, kwh, 1) for start, kwh in complete_hours(parse_quarter_hours(text))]
