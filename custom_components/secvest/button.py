from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    MODE_ACKNOWLEDGED,
    MODE_PARTSET_ALARM,
    MODE_SET_ALARM,
    MODE_UNSET_ALARM,
)
from .coordinator import _FAULT_TYPE_ZONE_OPEN

_ALARM_RAW_MODES = (MODE_UNSET_ALARM, MODE_SET_ALARM, MODE_PARTSET_ALARM)

_LOGGER = logging.getLogger(__name__)


def _faults(data: Any) -> list[dict[str, Any]]:
    faults = getattr(data, "faults", None)
    if isinstance(faults, list):
        return [item for item in faults if isinstance(item, dict)]
    return []


def _blocking_zone_ids(data: Any) -> list[str]:
    """Zonen-IDs, die laut aktueller Fault-Liste das Scharfschalten
    blockieren (Fault-Type "5000" = Zone offen, siehe coordinator.py) - genau
    die, die sich per api.set_zone_omitted() nachweislich loeschen lassen."""
    ids: list[str] = []
    for fault in _faults(data):
        if str(fault.get("type") or "") != _FAULT_TYPE_ZONE_OPEN:
            continue
        zone_id = str(fault.get("affects-zone") or "").strip()
        if zone_id and zone_id not in ids:
            ids.append(zone_id)
    return ids


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    api = data["api"]

    async_add_entities(
        [
            SecvestRefreshButton(coordinator, entry),
            SecvestAcknowledgeAlarmButton(coordinator, api, entry),
            SecvestOmitBlockingZonesButton(coordinator, api, entry),
            SecvestExtensiveRefreshButton(coordinator, entry),
        ]
    )


class SecvestBaseButton(ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry: ConfigEntry, unique_suffix: str, name: str) -> None:
        self.coordinator = coordinator
        self._entry = entry
        self._remove_coordinator_listener = None
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_name = name

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


class SecvestRefreshButton(SecvestBaseButton):
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "refresh_button", "Secvest Aktualisieren")

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


class SecvestExtensiveRefreshButton(SecvestBaseButton):
    """Loest die umfangreiche Wireless-Abfrage (RSSI/Batterie/Sabotage) sofort
    aus, unabhaengig vom naechtlichen Zeitplan - z. B. nach dem Einbau eines
    neuen Funkmelders, ohne bis zur konfigurierten Uhrzeit warten zu muessen."""

    _attr_icon = "mdi:wifi-refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "extensive_refresh_button", "Secvest RSSI/Wireless jetzt abfragen")

    @property
    def available(self) -> bool:
        return bool(getattr(self.coordinator, "wireless_enabled", False))

    async def async_press(self) -> None:
        await self.coordinator.async_trigger_extensive()


class SecvestAcknowledgeAlarmButton(SecvestBaseButton):
    """Quittiert einen aktiven Alarm (PUT {"state":"acknowledged"}) - der von
    der Secvest-API verlangte Zwischenschritt, bevor nach einem Alarm
    ueberhaupt "unset" gesendet werden kann (siehe thomasdanz/secvest-api:
    "unset only from set/partset or from acknowledged"; "acknowledged only
    while in alarm"). Bisher musste dieser Schritt am Bedienteil oder in der
    offiziellen App erfolgen, da "acknowledged" kein gueltiger Wert fuer den
    Service secvest_set_mode ist (bewusst nur als eigener Button, da dieser
    Schritt nur sinnvoll ist, waehrend ein Alarm tatsaechlich aktiv ist)."""

    _attr_icon = "mdi:alarm-light-off-outline"

    def __init__(self, coordinator, api, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "acknowledge_alarm", "Secvest Alarm quittieren")
        self._api = api

    @property
    def available(self) -> bool:
        d = self.coordinator.data
        return bool(d and d.raw_mode in _ALARM_RAW_MODES)

    async def async_press(self) -> None:
        try:
            await self._api.set_mode(MODE_ACKNOWLEDGED)
        except Exception as err:
            await self.coordinator.async_request_refresh()
            raise HomeAssistantError(f"Secvest: Alarm konnte nicht quittiert werden: {err}") from err

        await self.coordinator.async_request_refresh()


class SecvestOmitBlockingZonesButton(SecvestBaseButton):
    """Blendet alle Zonen aus, die laut Fault-Liste aktuell das Scharfschalten
    blockieren (Fault-Type "5000", 'Zone offen') - der einzige nachweislich
    funktionierende Weg, einen solchen Fault loszuwerden (siehe
    api.set_zone_omitted()). Ersetzt die fruehere(n) "Faults quittieren"-
    Button(s), die auf einem bei thomasdanz/secvest-api als unbestaetigt
    dokumentierten PUT /faults/{id}/ beruhten und vermutlich nie
    funktioniert haben (Geraet antwortet darauf wohl mit leerem 403, das
    bisher lautlos als Auth-Fehler verschluckt wurde). Fuer Fault-Typen ohne
    Zonenbezug (Batterie, Sabotage, Supervision) gibt es laut derselben Doku
    keinen bekannten Quittierungs-Call - die loesen sich nur durch Beheben
    der Ursache selbst auf. Die Ausblendung gilt nur fuer den naechsten
    Scharfschaltungszyklus (Firmware-Verhalten, nicht dauerhaft)."""

    _attr_icon = "mdi:eye-off-outline"

    def __init__(self, coordinator, api, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            "omit_blocking_zones",
            "Secvest blockierende Zonen ausblenden",
        )
        self._api = api

    async def async_press(self) -> None:
        zone_ids = _blocking_zone_ids(self.coordinator.data)
        if not zone_ids:
            _LOGGER.info("Secvest: keine blockierenden Zonen zum Ausblenden gefunden.")
            return

        errors: list[str] = []
        for zone_id in zone_ids:
            try:
                await self._api.set_zone_omitted(zone_id, True)
            except Exception as err:
                _LOGGER.warning("Secvest: Zone %s konnte nicht ausgeblendet werden: %r", zone_id, err)
                errors.append(f"Zone {zone_id}: {err}")

        await self.coordinator.async_request_refresh()

        if errors:
            raise HomeAssistantError(
                "Secvest: nicht alle blockierenden Zonen konnten ausgeblendet werden - "
                + "; ".join(errors)
            )
