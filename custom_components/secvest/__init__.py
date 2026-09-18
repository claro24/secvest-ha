from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SecvestApi, SecvestAuth, SecvestApiError
from .coordinator import SecvestCoordinator
from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_USER_CODE,
    CONF_WEB_USERNAME,
    CONF_WEB_PASSWORD,
    CONF_VERIFY_SSL,
    CONF_ZONES_INTERVAL,
    CONF_FAULTS_INTERVAL,
    CONF_MODE_INTERVAL,
    CONF_EXTENSIVE_INTERVAL,
    CONF_FETCH_OUTPUTS,
    CONF_RECONNECT_DELAY,
    CONF_RETRIES,
    CONF_BREAKER_THRESHOLD,
    CONF_BREAKER_COOLDOWN,
    DEFAULT_ZONES_INTERVAL,
    DEFAULT_FAULTS_INTERVAL,
    DEFAULT_MODE_INTERVAL,
    DEFAULT_EXTENSIVE_INTERVAL,
    DEFAULT_FETCH_OUTPUTS,
    DEFAULT_RECONNECT_DELAY,
    DEFAULT_RETRIES,
    DEFAULT_BREAKER_THRESHOLD,
    DEFAULT_BREAKER_COOLDOWN,
    SERVICE_SET_MODE,
    SERVICE_DUMP_DIAGNOSTICS,
    VALID_MODES,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["alarm_control_panel", "sensor", "binary_sensor", "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Secvest from a config entry."""
    session = async_get_clientsession(hass)

    # --- Read options (with sane defaults) ---
    options = entry.options
    zones_interval = options.get(CONF_ZONES_INTERVAL, DEFAULT_ZONES_INTERVAL)
    faults_interval = options.get(CONF_FAULTS_INTERVAL, DEFAULT_FAULTS_INTERVAL)
    mode_interval = options.get(CONF_MODE_INTERVAL, DEFAULT_MODE_INTERVAL)
    extensive_interval = options.get(CONF_EXTENSIVE_INTERVAL, DEFAULT_EXTENSIVE_INTERVAL)
    fetch_outputs = options.get(CONF_FETCH_OUTPUTS, DEFAULT_FETCH_OUTPUTS)
    reconnect_delay = options.get(CONF_RECONNECT_DELAY, DEFAULT_RECONNECT_DELAY)
    retries = options.get(CONF_RETRIES, DEFAULT_RETRIES)
    breaker_threshold = options.get(CONF_BREAKER_THRESHOLD, DEFAULT_BREAKER_THRESHOLD)
    breaker_cooldown = options.get(CONF_BREAKER_COOLDOWN, DEFAULT_BREAKER_COOLDOWN)

    # --- Auth ---
    auth = SecvestAuth(
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        user_code=entry.data[CONF_USER_CODE],
        web_username=options.get(CONF_WEB_USERNAME) or None,
        web_password=options.get(CONF_WEB_PASSWORD) or None,
    )

    # --- API ---
    api = SecvestApi(
        session=session,
        host=entry.data[CONF_HOST],
        auth=auth,
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, False),
        retries=retries,
    )

    # --- Coordinator ---
    coordinator = SecvestCoordinator(
        hass=hass,
        api=api,
        zones_interval_s=zones_interval,
        faults_interval_s=faults_interval,
        mode_interval_s=mode_interval,
        extensive_interval_min=extensive_interval,
        fetch_outputs=fetch_outputs,
        reconnect_delay_s=reconnect_delay,
        breaker_threshold=breaker_threshold,
        breaker_cooldown=breaker_cooldown,
        zone_name_map=options.get("zone_name_map", {}),
    )

    # --- Store runtime objects ---
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "entry": entry,
    }

    # --- Register service: secvest_set_mode ---
    async def handle_set_mode(call: ServiceCall) -> None:
        mode = call.data.get("mode")

        if mode not in VALID_MODES:
            raise HomeAssistantError(
                f"Invalid mode: {mode}. Use one of {sorted(VALID_MODES)}"
            )

        # Refresh before switching (check open zones)
        await coordinator.async_request_refresh()

        if not coordinator.data:
            raise HomeAssistantError("Secvest status currently unknown.")

        if mode in ("set", "partset") and coordinator.data.open_zone_names:
            raise HomeAssistantError(
                coordinator.data.open_zones_spoken
                or "Offene Zonen verhindern Aktivierung."
            )

        try:
            await api.set_mode(mode)

        except asyncio.TimeoutError:
            raise HomeAssistantError(
                "Secvest antwortet nicht (Timeout). Bitte später erneut versuchen."
            )

        except SecvestApiError as err:
            raise HomeAssistantError(f"Secvest API Fehler: {err}") from err

        except aiohttp.ClientResponseError as err:
            raise HomeAssistantError(f"Secvest HTTP Fehler: {err.status}") from err

        # Refresh after switching
        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_MODE,
        handle_set_mode,
    )

    async def handle_dump_diagnostics(call: ServiceCall) -> None:
        include_outputs = bool(call.data.get("include_outputs", False))
        filename = str(call.data.get("filename") or "secvest_diagnostics.json")
        if "/" in filename or "\\" in filename:
            raise HomeAssistantError("filename must not contain a path")

        diagnostics: dict[str, object] = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "entries": [],
        }

        for entry_id, store in hass.data.get(DOMAIN, {}).items():
            entry_api = store["api"]
            entry_coordinator = store["coordinator"]
            entry_obj = store.get("entry")

            item: dict[str, object] = {
                "entry_id": entry_id,
                "title": getattr(entry_obj, "title", None),
                "host": getattr(entry_obj, "data", {}).get(CONF_HOST) if entry_obj else None,
                "current_data": None,
                "live": {},
                "errors": {},
            }

            data = getattr(entry_coordinator, "data", None)
            if data is not None:
                item["current_data"] = {
                    "raw_mode": getattr(data, "raw_mode", None),
                    "human_mode": getattr(data, "human_mode", None),
                    "zones": getattr(data, "zones", None),
                    "faults": getattr(data, "faults", None),
                    "outputs": getattr(data, "outputs", None),
                    "open_zone_names": getattr(data, "open_zone_names", None),
                    "available": getattr(data, "available", None),
                    "last_error": getattr(data, "last_error", None),
                }

            for key, coro in (
                ("mode", entry_api.get_mode()),
                ("zones", entry_api.get_zones()),
                ("wireless_zones_status", entry_api.get_wireless_zones_status()),
                ("wireless_zones_status_debug", entry_api.get_wireless_zones_status_debug()),
                ("faults", entry_api.get_faults()),
            ):
                try:
                    item["live"][key] = await coro  # type: ignore[index]
                except Exception as err:
                    item["errors"][key] = repr(err)  # type: ignore[index]

            if include_outputs:
                try:
                    item["live"]["outputs"] = await entry_api.get_outputs()  # type: ignore[index]
                except Exception as err:
                    item["errors"]["outputs"] = repr(err)  # type: ignore[index]

            diagnostics["entries"].append(item)  # type: ignore[union-attr]

        path = hass.config.path(filename)

        def _write_file() -> None:
            with open(path, "w", encoding="utf-8") as file:
                json.dump(diagnostics, file, ensure_ascii=False, indent=2, default=str)

        await hass.async_add_executor_job(_write_file)
        _LOGGER.warning("Secvest diagnostics written to %s", path)

    hass.services.async_register(
        DOMAIN,
        SERVICE_DUMP_DIAGNOSTICS,
        handle_dump_diagnostics,
    )

    # --- Forward platforms ---
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    coordinator.async_start()
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload Secvest when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Secvest config entry."""
    store = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if store is not None:
        await store["coordinator"].async_stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_SET_MODE)
            hass.services.async_remove(DOMAIN, SERVICE_DUMP_DIAGNOSTICS)

    return unload_ok
