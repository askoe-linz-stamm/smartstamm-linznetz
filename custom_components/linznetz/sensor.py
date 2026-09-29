"""Shows how current the imported statistics are."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
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
    """Add the sensor."""
    async_add_entities([DataUntilSensor(entry.runtime_data)])


class DataUntilSensor(CoordinatorEntity[LinzNetzCoordinator], SensorEntity):
    """End of the newest hour in the statistics; stays unknown before the first value."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True
    _attr_translation_key = "data_until"

    def __init__(self, coordinator: LinzNetzCoordinator) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_data_until"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Linz Netz Stromzähler",
            manufacturer="Linz Netz",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data
