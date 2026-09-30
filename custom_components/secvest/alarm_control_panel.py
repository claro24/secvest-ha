from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
)
from homeassistant.components.alarm_control_panel.const import AlarmControlPanelState
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import SecvestApiError
from .const import ALARM_MEMORY_MODES, DOMAIN, RAW_TO_HA_STATE

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    async_add_entities([SecvestAlarm(coordinator, entry)], True)


class SecvestAlarm(AlarmControlPanelEntity):
    """Alarm control panel for ABUS Secvest."""

    _attr_name = "Secvest Alarm"
    _attr_icon = "mdi:shield-home"

    # PIN/User-Code wird backendseitig gesendet -> kein UI-Code-Dialog
    _attr_code_arm_required = False
    _attr_code_disarm_required = False

    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
    )

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._remove_coordinator_listener = None
        self._attr_unique_id = f"{entry.entry_id}_alarm"
        self._warned_unknown_raw_modes: set[str] = set()

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        d = self.coordinator.data
        if not d or not d.raw_mode:
            return None
        ha_state = RAW_TO_HA_STATE.get(d.raw_mode)
        if ha_state is None:
            if d.raw_mode not in self._warned_unknown_raw_modes:
                self._warned_unknown_raw_modes.add(d.raw_mode)
                _LOGGER.warning(
                    "Secvest: unbekannter Alarmzustand-Rohwert '%s' - bitte melden, "
                    "damit das Mapping ergaenzt werden kann",
                    d.raw_mode,
                )
            return None
        return AlarmControlPanelState(ha_state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        raw_mode = self.coordinator.data.raw_mode if self.coordinator.data else None
        return {
            "raw_mode": raw_mode,
            "alarm_memory": raw_mode in ALARM_MEMORY_MODES if raw_mode else False,
        }

    # -----------------------
    # New-style async methods
    # -----------------------
    async def async_arm_home(self, code: str | None = None) -> None:
        await self._arm_with_live_zone_check("partset")

    async def async_arm_away(self, code: str | None = None) -> None:
        await self._arm_with_live_zone_check("set")

    async def async_disarm(self, code: str | None = None) -> None:
        await self._set_mode("unset")

    # -------------------------------------------------
    # Old-style sync methods (HA may still call these)
    # -------------------------------------------------
    def alarm_arm_home(self, code: str | None = None) -> None:
        fut = asyncio.run_coroutine_threadsafe(self.async_arm_home(code), self.hass.loop)
        return fut.result()

    def alarm_arm_away(self, code: str | None = None) -> None:
        fut = asyncio.run_coroutine_threadsafe(self.async_arm_away(code), self.hass.loop)
        return fut.result()

    def alarm_disarm(self, code: str | None = None) -> None:
        fut = asyncio.run_coroutine_threadsafe(self.async_disarm(code), self.hass.loop)
        return fut.result()

    # -----------------------
    # Internal helpers
    # -----------------------
    async def _arm_with_live_zone_check(self, mode: str) -> None:
        """
        Always refresh zones live (may take longer), then decide if arming is allowed.
        This prevents arming being blocked by stale zone cache.
        """
        zones = await self.coordinator.async_get_zones_live()

        # Friendly-name mapping from coordinator (keeps naming consistent)
        from .coordinator import normalize_name  # local import to avoid cycles

        zone_name_map = getattr(self.coordinator, "_zone_name_map", {}) or {}

        zones_dict = {}
        open_names: list[str] = []

        for z in zones:
            if not isinstance(z, dict):
                continue
            raw_name = z.get("name")
            state = z.get("state")
            if not isinstance(raw_name, str) or not isinstance(state, str):
                continue

            key = normalize_name(raw_name)

            friendly = zone_name_map.get(key)
            if not friendly:
                # fallback: readable from normalized key
                friendly = key.replace("_", " ")

            zones_dict[key] = {"name": raw_name, "state": state, "friendly_name": friendly}

            if state == "open":
                open_names.append(friendly)

        # Update HA entities immediately with live zone snapshot (no stale UI)
        d = self.coordinator.data
        if d:
            open_csv = ", ".join(open_names)
            spoken = (
                ("Ich konnte die Alarmanlage nicht aktivieren, bitte schließe: " + open_csv + ".")
                if open_names
                else ""
            )
            new_data = type(d)(
                raw_mode=d.raw_mode,
                human_mode=d.human_mode,
                zones=zones_dict,
                faults=d.faults,
                outputs=d.outputs,
                open_zone_names=open_names,
                open_zones_csv=open_csv,
                open_zones_spoken=spoken,
                available=d.available,
                last_error=d.last_error,
            )
            self.coordinator.async_set_updated_data(new_data)

        # If open zones -> abort with friendly message
        if open_names:
            raise HomeAssistantError(
                "Ich konnte die Alarmanlage nicht aktivieren, bitte schließe: " + ", ".join(open_names)
            )

        await self._set_mode(mode)

        # Refresh after switching to get confirmed mode
        await self.coordinator.async_request_refresh()

    async def _set_mode(self, mode: str) -> None:
        try:
            await self.coordinator.api.set_mode(mode)
            await self.coordinator.async_request_refresh()

        except asyncio.TimeoutError:
            raise HomeAssistantError("Secvest antwortet nicht (Timeout). Bitte erneut versuchen.")

        except SecvestApiError as err:
            raise HomeAssistantError(f"Secvest API Fehler: {err}") from err

        except aiohttp.ClientResponseError as err:
            raise HomeAssistantError(f"Secvest HTTP Fehler: {err.status}") from err

        except Exception as err:
            _LOGGER.exception("Unexpected error while setting Secvest mode to %s", mode)
            raise HomeAssistantError(f"Unbekannter Fehler: {err!r}") from err

    async def async_added_to_hass(self) -> None:
        self._remove_coordinator_listener = self.coordinator.async_add_listener(
            self.async_write_ha_state
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_coordinator_listener:
            self._remove_coordinator_listener()
            self._remove_coordinator_listener = None
