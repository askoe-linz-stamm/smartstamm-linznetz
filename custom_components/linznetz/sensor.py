"""How current the imported statistics are and whether the last run worked."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LinzNetzConfigEntry, LinzNetzCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinzNetzConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors."""
    coordinator = entry.runtime_data
    async_add_entities([DataUntilSensor(coordinator), StatusSensor(coordinator)])


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


class DataUntilSensor(_LinzNetzSensor):
    """End of the newest hour in the statistics; unknown before the first value."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: LinzNetzCoordinator) -> None:
        super().__init__(coordinator, "data_until")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data


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
