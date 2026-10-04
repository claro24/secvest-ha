from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_TIER_LABELS = {
    "zones": "Zonen",
    "faults": "Fehler",
    "mode": "Alarmzustand",
    "extensive": "Umfangreiche Abfrage",
}


def _faults(data: Any) -> list[dict[str, Any]]:
    faults = getattr(data, "faults", None)
    if isinstance(faults, list):
        return [item for item in faults if isinstance(item, dict)]
    return []


def _outputs(data: Any) -> list[dict[str, Any]]:
    outputs = getattr(data, "outputs", None)
    if isinstance(outputs, list):
        return [item for item in outputs if isinstance(item, dict)]
    return []


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities: list[SensorEntity] = [
        SecvestSimpleSensor(coordinator, entry, "Secvest Mode (Raw)", "raw_mode"),
        SecvestSimpleSensor(coordinator, entry, "Secvest Mode (DE)", "human_mode"),
        SecvestSimpleSensor(coordinator, entry, "Open Zones (CSV)", "open_zones_csv"),
        SecvestSimpleSensor(coordinator, entry, "Open Zones Spoken", "open_zones_spoken"),
        SecvestDerivedSensor(coordinator, entry, "Open Zones Count", "open_zones_count"),
        SecvestDerivedSensor(coordinator, entry, "Secvest Faults Anzahl", "fault_count"),
        SecvestFaultListSensor(coordinator, entry),
        SecvestDerivedSensor(coordinator, entry, "Secvest Outputs Anzahl", "output_count"),
        SecvestSimpleSensor(coordinator, entry, "Last Error", "last_error"),
    ]
    for tier in _TIER_LABELS:
        entities.append(SecvestResponseTimeSensor(coordinator, entry, tier))
        entities.append(SecvestWaitTimeSensor(coordinator, entry, tier))

    async_add_entities(entities, True)


class SecvestBaseEntity:
    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._entry = entry
        self._remove_coordinator_listener = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="ABUS Secvest",
            manufacturer="ABUS",
            model="Secvest",
            configuration_url=self._entry.data.get("host"),
        )

    async def async_added_to_hass(self) -> None:
        self._remove_coordinator_listener = self.coordinator.async_add_listener(
            self.async_write_ha_state
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_coordinator_listener:
            self._remove_coordinator_listener()
            self._remove_coordinator_listener = None


class SecvestSimpleSensor(SecvestBaseEntity, SensorEntity):
    def __init__(self, coordinator, entry: ConfigEntry, name: str, key: str) -> None:
        SecvestBaseEntity.__init__(self, coordinator, entry)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_sensor_{key}"

    @property
    def native_value(self) -> Any:
        d = self.coordinator.data
        if not d:
            if self._key == "last_error":
                return "OK"
            return None

        val = getattr(d, self._key, None)
        if self._key == "last_error":
            return val or "OK"
        return val


class SecvestDerivedSensor(SecvestBaseEntity, SensorEntity):
    def __init__(self, coordinator, entry: ConfigEntry, name: str, key: str) -> None:
        SecvestBaseEntity.__init__(self, coordinator, entry)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_sensor_{key}"
        if key in {"fault_count", "output_count"}:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> Any:
        d = self.coordinator.data
        if not d:
            return 0

        if self._key == "open_zones_count":
            return len(d.open_zone_names) if d.open_zone_names else 0
        if self._key == "fault_count":
            return len(_faults(d))
        if self._key == "output_count":
            return len(_outputs(d))
        return None


class SecvestFaultListSensor(SecvestBaseEntity, SensorEntity):
    _attr_name = "Secvest Faults Liste"
    _attr_icon = "mdi:format-list-bulleted"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        SecvestBaseEntity.__init__(self, coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_sensor_fault_list"

    @property
    def native_value(self) -> str:
        faults = _faults(self.coordinator.data)
        values = [
            str(fault.get("ui-string") or fault.get("text") or fault.get("name"))
            for fault in faults
            if fault.get("ui-string") or fault.get("text") or fault.get("name")
        ]
        return ", ".join(values) if values else "OK"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"faults": _faults(self.coordinator.data)}


def _tier_available(coordinator, tier: str) -> bool:
    """Die umfangreiche Abfrage laeuft gar nicht, wenn keine Web-Credentials
    (RSSI) konfiguriert sind - dann zeigen ihre Diagnose-Sensoren "nicht
    verfuegbar" statt dauerhaft einen leeren/unbekannten Wert."""
    if tier == "extensive":
        return bool(getattr(coordinator, "wireless_enabled", True))
    return True


class SecvestResponseTimeSensor(SecvestBaseEntity, SensorEntity):
    """Gleitende durchschnittliche reine API-Antwortzeit (letzte 20 Werte,
    ohne Wartezeit auf das gemeinsame Lock) einer Abfrage-Ebene."""

    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry: ConfigEntry, tier: str) -> None:
        SecvestBaseEntity.__init__(self, coordinator, entry)
        self._tier = tier
        self._attr_name = f"Secvest Antwortzeit {_TIER_LABELS[tier]} (Ø)"
        self._attr_unique_id = f"{entry.entry_id}_sensor_response_time_{tier}"

    @property
    def available(self) -> bool:
        return _tier_available(self.coordinator, self._tier)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.average_response_time_ms(self._tier)


class SecvestWaitTimeSensor(SecvestBaseEntity, SensorEntity):
    """Gleitende durchschnittliche Wartezeit (letzte 20 Werte) einer Ebene auf
    das gemeinsame Lock, weil eine andere Ebene gerade eine Abfrage laufen
    hatte. Hoch heisst haeufige Kollisionen, nicht zwangsweise ein Problem
    der Secvest selbst - der Request wird dadurch nur verzoegert, nie verworfen."""

    _attr_icon = "mdi:timer-sand"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry: ConfigEntry, tier: str) -> None:
        SecvestBaseEntity.__init__(self, coordinator, entry)
        self._tier = tier
        self._attr_name = f"Secvest Wartezeit {_TIER_LABELS[tier]} (Ø)"
        self._attr_unique_id = f"{entry.entry_id}_sensor_wait_time_{tier}"

    @property
    def available(self) -> bool:
        return _tier_available(self.coordinator, self._tier)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.average_wait_time_ms(self._tier)
