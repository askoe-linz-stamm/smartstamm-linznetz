"""How current the imported statistics are and whether the last run worked."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import LinzNetzConfigEntry, LinzNetzCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinzNetzConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            DataUntilSensor(coordinator),
            LastSuccessfulFetchSensor(coordinator),
            NextFetchSensor(coordinator),
            StatusSensor(coordinator),
        ]
    )


class _LinzNetzSensor(CoordinatorEntity[LinzNetzCoordinator], SensorEntity):
    """Stays available when a run fails: the statistics keep their last state."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LinzNetzCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Linz Netz Stromzähler",
            manufacturer="Linz Netz",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        return True


class DataUntilSensor(_LinzNetzSensor, RestoreEntity):
    """End of the newest hour in the statistics; unknown before the first value."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: LinzNetzCoordinator) -> None:
        super().__init__(coordinator, "data_until")

    async def async_added_to_hass(self) -> None:
        """Keep the previous data horizon if the first fetch after a restart fails."""
        if not self.coordinator.last_update_success and self.coordinator.data is None:
            if previous := await self.async_get_last_state():
                restored = dt_util.parse_datetime(previous.state)
                if restored is not None and restored.tzinfo is not None:
                    self.coordinator.data = restored
        await super().async_added_to_hass()

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data


class LastSuccessfulFetchSensor(_LinzNetzSensor, RestoreEntity):
    """Completion time of the last successful fetch, including unchanged exports."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: LinzNetzCoordinator) -> None:
        super().__init__(coordinator, "last_successful_fetch")

    async def async_added_to_hass(self) -> None:
        """Retain the last success when the portal is unavailable after a restart."""
        if self.coordinator.last_successful_fetch is None:
            if previous := await self.async_get_last_state():
                restored = dt_util.parse_datetime(previous.state)
                if restored is not None and restored.tzinfo is not None:
                    self.coordinator.last_successful_fetch = restored
        await super().async_added_to_hass()

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_successful_fetch


class NextFetchSensor(_LinzNetzSensor):
    """Planned polling time; unknown when no periodic fetch is scheduled."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: LinzNetzCoordinator) -> None:
        super().__init__(coordinator, "next_fetch")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.next_fetch


class StatusSensor(_LinzNetzSensor):
    """Result of the last run.

    ``portal_changed`` with a stable ``error_code`` means the portal no longer
    works as expected; SmartStamm turns lasting ones into GitHub issues.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = ["ok", "portal_changed", "login_rejected", "connection_error"]

    def __init__(self, coordinator: LinzNetzCoordinator) -> None:
        super().__init__(coordinator, "status")

    @property
    def native_value(self) -> str:
        problem = self.coordinator.problem
        return "ok" if problem is None else problem.kind

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        problem = self.coordinator.problem
        if problem is None:
            return {}
        return {
            "error_code": problem.code,
            "since": problem.since.isoformat(),
            "failed_runs": problem.failed_runs,
        }
