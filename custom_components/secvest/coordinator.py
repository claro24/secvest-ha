from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SecvestApi, SecvestApiError
from .const import STATE_TRANSLATIONS

_LOGGER = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    return (
        name.replace(".", "_")
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace("ä", "ae")
        .replace("Ä", "ae")
        .replace("ö", "oe")
        .replace("Ö", "oe")
        .replace("ü", "ue")
        .replace("Ü", "ue")
        .replace("ß", "ss")
    )


@dataclass
class SecvestData:
    raw_mode: str | None
    human_mode: str | None
    zones: dict[str, dict[str, Any]]  # key -> {name, state}
    faults: list[dict[str, Any]]
    outputs: list[dict[str, Any]]
    open_zone_names: list[str]
    open_zones_csv: str
    open_zones_spoken: str
    available: bool
    last_error: str | None


def make_spoken_zone_list(zones: list[str]) -> str:
    if not zones:
        return ""
    if len(zones) == 1:
        return f"Ich konnte die Alarmanlage nicht aktivieren, bitte schließe: {zones[0]}."
    last = zones[-1]
    head = ", ".join(zones[:-1])
    return (
        "Ich konnte die Alarmanlage nicht aktivieren, bitte folgende Fenster und Türen schließen: "
        f"{head} und {last}."
    )


class SecvestCoordinator(DataUpdateCoordinator[SecvestData]):
    """Haelt eine dauerhafte Verbindung offen und pollt vier unabhaengige,
    getrennt konfigurierbare Ebenen (Zonen, Fehler, Alarmzustand, umfangreiche
    Wireless-Abfrage). Ein gemeinsames Lock sorgt dafuer, dass nie zwei
    Abfragen gleichzeitig laufen; ist das Geraet gerade beschaeftigt, wird der
    Tick uebersprungen statt eingereiht (keine Warteschlange, keine
    Ueberlastung)."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: SecvestApi,
        *,
        zones_interval_s: int,
        faults_interval_s: int,
        mode_interval_s: int,
        extensive_interval_min: int,
        fetch_outputs: bool,
        zone_name_map: dict[str, str] | None = None,
        breaker_threshold: int = 5,
        breaker_cooldown: int = 300,
        reconnect_delay_s: int = 10,
    ) -> None:
        super().__init__(hass, logger=_LOGGER, name="Secvest Coordinator", update_interval=None)
        self.api = api

        self._zone_name_map = zone_name_map or {}
        self._zones_interval = max(1, int(zones_interval_s))
        self._faults_interval = max(1, int(faults_interval_s))
        self._mode_interval = max(1, int(mode_interval_s))
        self._extensive_interval = max(60, int(extensive_interval_min) * 60)
        self._fetch_outputs = fetch_outputs
        self._reconnect_delay = max(1, int(reconnect_delay_s))

        # Circuit breaker: Eskalationsstufe, falls der schnelle Reconnect
        # wiederholt fehlschlaegt (echter Geraeteausfall statt Einzelaussetzer)
        self._consecutive_failures = 0
        self._breaker_until = 0.0
        self._breaker_threshold = max(1, int(breaker_threshold))
        self._breaker_cooldown = max(10, int(breaker_cooldown))
        self._last_error: str | None = None

        self._wireless_cache: dict[int, dict[str, Any]] = {}
        self._request_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []

    # -------------------------
    # Lifecycle
    # -------------------------

    def async_start(self) -> None:
        """Startet die vier unabhaengigen Poll-Loops."""
        if self._tasks:
            return
        loops: tuple[tuple[str, Callable[[], Awaitable[None]], float], ...] = (
            ("zones", self._fetch_zones, self._zones_interval),
            ("faults", self._fetch_faults, self._faults_interval),
            ("mode", self._fetch_mode, self._mode_interval),
            ("extensive", self._fetch_extensive, self._extensive_interval),
        )
        for name, fetch_fn, interval_s in loops:
            self._tasks.append(self._create_task(self._loop(name, fetch_fn, interval_s), f"secvest_{name}_poll"))

    def _create_task(self, coro: Awaitable[None], name: str) -> asyncio.Task[None]:
        create_background_task = getattr(self.hass, "async_create_background_task", None)
        if create_background_task is not None:
            return create_background_task(coro, name)
        return self.hass.loop.create_task(coro, name=name)

    async def async_stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # -------------------------
    # Scheduling: ein Request gleichzeitig, ueberspringen statt einreihen
    # -------------------------

    async def _loop(self, name: str, fetch_fn: Callable[[], Awaitable[None]], interval_s: float) -> None:
        while True:
            sleep_for = await self._run_tier(name, fetch_fn, interval_s)
            await asyncio.sleep(sleep_for)

    async def _run_tier(self, name: str, fetch_fn: Callable[[], Awaitable[None]], interval_s: float) -> float:
        now = time.time()
        if now < self._breaker_until:
            return max(1.0, min(interval_s, self._breaker_until - now))

        if self._request_lock.locked():
            _LOGGER.debug("Secvest %s-Abfrage ausgesetzt (Geraet gerade beschaeftigt)", name)
            return interval_s

        try:
            async with self._request_lock:
                await fetch_fn()
        except (asyncio.TimeoutError, SecvestApiError) as err:
            self._handle_failure(name, err)
            return self._reconnect_delay
        except Exception as err:  # unerwarteter Fehler: nicht die Loop sterben lassen
            _LOGGER.exception("Secvest %s-Abfrage fehlgeschlagen (unerwartet)", name)
            self._handle_failure(name, err)
            return self._reconnect_delay

        self._consecutive_failures = 0
        self._breaker_until = 0.0
        self._last_error = None
        return interval_s

    def _handle_failure(self, name: str, err: Exception) -> None:
        self._consecutive_failures += 1
        self._last_error = repr(err)

        if self._consecutive_failures >= self._breaker_threshold:
            self._breaker_until = time.time() + self._breaker_cooldown
            _LOGGER.warning(
                "Secvest Circuit Breaker aktiv fuer %ss nach %s Fehlversuchen (zuletzt %s): %s",
                self._breaker_cooldown,
                self._consecutive_failures,
                name,
                self._last_error,
            )
        else:
            _LOGGER.warning(
                "Secvest %s: Verbindungsabbruch (%s/%s), Reconnect in %ss: %s",
                name,
                self._consecutive_failures,
                self._breaker_threshold,
                self._reconnect_delay,
                self._last_error,
            )

        if self.data is not None:
            self.async_set_updated_data(replace(self.data, available=False, last_error=self._last_error))

    async def async_get_zones_live(self) -> list[dict[str, Any]]:
        """Live-Zonenabfrage fuer Aktionen wie Scharfschalten - seriell mit den
        Poll-Loops ueber dasselbe Lock, damit nie zwei Requests parallel laufen."""
        async with self._request_lock:
            return await self.api.get_zones()

    # -------------------------
    # Manueller Voll-Refresh (Button, nach Arm/Disarm, Service-Calls)
    # -------------------------

    async def _async_update_data(self) -> SecvestData:
        try:
            async with self._request_lock:
                await self._fetch_mode()
                await self._fetch_zones()
                await self._fetch_faults()
        except (asyncio.TimeoutError, SecvestApiError) as err:
            self._handle_failure("manual", err)
        except Exception as err:
            _LOGGER.exception("Secvest manueller Refresh fehlgeschlagen: %s", err)
            self._handle_failure("manual", err)

        if self.data is None:
            raise UpdateFailed(self._last_error or "Update failed")
        return self.data

    # -------------------------
    # Die vier Abfrage-Ebenen
    # -------------------------

    def _base_data(self) -> SecvestData:
        if self.data is not None:
            return self.data
        return SecvestData(
            raw_mode=None,
            human_mode=None,
            zones={},
            faults=[],
            outputs=[],
            open_zone_names=[],
            open_zones_csv="",
            open_zones_spoken="",
            available=False,
            last_error=None,
        )

    async def _fetch_zones(self) -> None:
        base = self._base_data()
        zones_payload = await self.api.get_zones()

        outputs = base.outputs
        if self._fetch_outputs:
            try:
                outputs = await self.api.get_outputs()
            except Exception as err:
                _LOGGER.debug("Secvest optionale Ausgaenge-Abfrage fehlgeschlagen: %r", err)

        zones_dict: dict[str, dict[str, Any]] = {}
        for idx, zone in enumerate(zones_payload):
            name = zone.get("name")
            state = zone.get("state")
            if not isinstance(name, str) or not isinstance(state, str):
                continue
            key = normalize_name(name)
            zone_data = dict(zone)
            zone_data["name"] = name
            zone_data["state"] = state
            zone_data.update(self._wireless_cache.get(idx, {}))
            zones_dict[key] = zone_data

        open_zone_names: list[str] = []
        for key, zone in zones_dict.items():
            if zone.get("state") == "open":
                friendly = self._zone_name_map.get(key) or self._zone_name_map.get(zone.get("name", ""))
                if not friendly:
                    friendly = key.replace("_", " ")
                open_zone_names.append(friendly)

        self.async_set_updated_data(
            replace(
                base,
                zones=zones_dict,
                outputs=outputs,
                open_zone_names=open_zone_names,
                open_zones_csv=", ".join(open_zone_names),
                open_zones_spoken=make_spoken_zone_list(open_zone_names),
                available=True,
                last_error=None,
            )
        )

    async def _fetch_faults(self) -> None:
        base = self._base_data()
        faults_payload = await self.api.get_faults()
        self.async_set_updated_data(replace(base, faults=faults_payload, available=True, last_error=None))

    async def _fetch_mode(self) -> None:
        base = self._base_data()
        raw_mode = await self.api.get_mode()
        human_mode = STATE_TRANSLATIONS.get(raw_mode, "Unbekannt")
        self.async_set_updated_data(
            replace(base, raw_mode=raw_mode, human_mode=human_mode, available=True, last_error=None)
        )

    async def _fetch_extensive(self) -> None:
        # Liefert RSSI/Batterie/Sabotage je Zonen-Index; wird von der naechsten
        # Zonen-Abfrage automatisch mit uebernommen (kein eigener Zonen-Request hier).
        self._wireless_cache = await self.api.get_wireless_zones_status()
        base = self._base_data()
        self.async_set_updated_data(replace(base, available=True, last_error=None))
