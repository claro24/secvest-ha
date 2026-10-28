from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _faults(data: Any) -> list[dict[str, Any]]:
    faults = getattr(data, "faults", None)
    if isinstance(faults, list):
        return [item for item in faults if isinstance(item, dict)]
    return []


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
            SecvestAckAllFaultsButton(coordinator, api, entry),
            SecvestAckBlockingFaultsButton(coordinator, api, entry),
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


class SecvestAckFaultsButton(SecvestBaseButton):
    _attr_icon = "mdi:check-circle-outline"

    def __init__(
        self,
        coordinator,
        api,
        entry: ConfigEntry,
        unique_suffix: str,
        name: str,
        blocking_only: bool,
    ) -> None:
        super().__init__(coordinator, entry, unique_suffix, name)
        self._api = api
        self._blocking_only = blocking_only

    async def async_press(self) -> None:
        ids: list[str] = []
        for fault in _faults(self.coordinator.data):
            if self._blocking_only and not bool(fault.get("prevents-set")):
                continue
            fault_id = fault.get("id")
            if fault_id is not None:
                ids.append(str(fault_id))

        if not ids:
            _LOGGER.info("No Secvest faults to acknowledge.")
            return

        for fault_id in ids:
            try:
                await self._api.ack_fault(fault_id)
            except Exception as err:
                _LOGGER.warning("Failed to acknowledge Secvest fault %s: %r", fault_id, err)

        await self.coordinator.async_request_refresh()


class SecvestAckAllFaultsButton(SecvestAckFaultsButton):
    def __init__(self, coordinator, api, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            api,
            entry,
            "ack_all_faults",
            "Secvest Faults quittieren",
            blocking_only=False,
        )


class SecvestAckBlockingFaultsButton(SecvestAckFaultsButton):
    def __init__(self, coordinator, api, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            api,
            entry,
            "ack_blocking_faults",
            "Secvest blockierende Faults quittieren",
            blocking_only=True,
        )
