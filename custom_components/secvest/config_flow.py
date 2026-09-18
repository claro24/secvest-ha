from __future__ import annotations

import asyncio
import logging
import socket
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry

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
    DEFAULT_VERIFY_SSL,
    DEFAULT_ZONES_INTERVAL,
    DEFAULT_FAULTS_INTERVAL,
    DEFAULT_MODE_INTERVAL,
    DEFAULT_EXTENSIVE_INTERVAL,
    DEFAULT_FETCH_OUTPUTS,
    DEFAULT_RECONNECT_DELAY,
    DEFAULT_RETRIES,
    DEFAULT_BREAKER_THRESHOLD,
    DEFAULT_BREAKER_COOLDOWN,
)

_LOGGER = logging.getLogger(__name__)


def _connection_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST)): str,  # z.B. https://192.168.2.22:4433
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME)): str,
            vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD)): str,
            vol.Required(CONF_USER_CODE, default=defaults.get(CONF_USER_CODE)): str,
            vol.Optional(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)): bool,
        }
    )


class SecvestConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        return await self._async_step_connection(user_input, step_id="user", existing_entry=None)

    async def async_step_reconfigure(self, user_input=None):
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self._async_step_connection(user_input, step_id="reconfigure", existing_entry=entry)

    async def _async_step_connection(self, user_input, *, step_id: str, existing_entry: ConfigEntry | None):
        errors: dict[str, str] = {}
        defaults = dict(existing_entry.data) if existing_entry else {}
        schema = _connection_schema(defaults if user_input is None else user_input)

        if user_input is None:
            return self.async_show_form(step_id=step_id, data_schema=schema)

        # --- Robust connectivity test: TCP only (no HTTP/TLS) ---
        try:
            host_input = user_input[CONF_HOST].strip()
            parsed = urlparse(host_input if "://" in host_input else f"https://{host_input}")

            hostname = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == "https" else 80)

            if not hostname:
                errors["base"] = "cannot_connect"
            else:
                await self._async_test_tcp_socket(hostname, port, timeout=20, retries=3)

        except Exception as e:
            _LOGGER.warning("Secvest config_flow TCP test failed: %s", e, exc_info=True)
            errors["base"] = "cannot_connect"

        if errors:
            return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

        if existing_entry is not None:
            self.hass.config_entries.async_update_entry(existing_entry, data=user_input)
            await self.hass.config_entries.async_reload(existing_entry.entry_id)
            return self.async_abort(reason="reconfigure_successful")

        await self.async_set_unique_id(user_input[CONF_HOST])
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"Secvest ({user_input[CONF_HOST]})",
            data=user_input,
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry):
        return SecvestOptionsFlowHandler()

    async def _async_test_tcp_socket(self, host: str, port: int, timeout: int = 20, retries: int = 3) -> None:
        """TCP test via socket.create_connection in executor. Raises on failure."""
        last_exc: Exception | None = None

        def _connect_blocking():
            with socket.create_connection((host, port), timeout=timeout):
                return True

        for attempt in range(1, retries + 1):
            try:
                await self.hass.async_add_executor_job(_connect_blocking)
                return
            except Exception as e:
                last_exc = e
                _LOGGER.warning("Secvest TCP test attempt %s/%s failed: %s", attempt, retries, e)
                await asyncio.sleep(1.0)

        raise last_exc or TimeoutError("TCP connect failed")


class SecvestOptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ZONES_INTERVAL, default=opts.get(CONF_ZONES_INTERVAL, DEFAULT_ZONES_INTERVAL)
                ): int,
                vol.Optional(
                    CONF_FAULTS_INTERVAL, default=opts.get(CONF_FAULTS_INTERVAL, DEFAULT_FAULTS_INTERVAL)
                ): int,
                vol.Optional(
                    CONF_MODE_INTERVAL, default=opts.get(CONF_MODE_INTERVAL, DEFAULT_MODE_INTERVAL)
                ): int,
                vol.Optional(
                    CONF_EXTENSIVE_INTERVAL,
                    default=opts.get(CONF_EXTENSIVE_INTERVAL, DEFAULT_EXTENSIVE_INTERVAL),
                ): int,
                vol.Optional(
                    CONF_FETCH_OUTPUTS, default=opts.get(CONF_FETCH_OUTPUTS, DEFAULT_FETCH_OUTPUTS)
                ): bool,
                vol.Optional(
                    CONF_RECONNECT_DELAY, default=opts.get(CONF_RECONNECT_DELAY, DEFAULT_RECONNECT_DELAY)
                ): int,
                vol.Optional(CONF_WEB_USERNAME, default=opts.get(CONF_WEB_USERNAME, "")): str,
                vol.Optional(CONF_WEB_PASSWORD, default=opts.get(CONF_WEB_PASSWORD, "")): str,
                vol.Optional(CONF_RETRIES, default=opts.get(CONF_RETRIES, DEFAULT_RETRIES)): int,
                vol.Optional(
                    CONF_BREAKER_THRESHOLD,
                    default=opts.get(CONF_BREAKER_THRESHOLD, DEFAULT_BREAKER_THRESHOLD),
                ): int,
                vol.Optional(
                    CONF_BREAKER_COOLDOWN,
                    default=opts.get(CONF_BREAKER_COOLDOWN, DEFAULT_BREAKER_COOLDOWN),
                ): int,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
