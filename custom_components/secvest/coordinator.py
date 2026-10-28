from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SecvestApi, SecvestApiError
from .const import DEFAULT_EXTENSIVE_TIME, STATE_TRANSLATIONS

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
    last_log_user: str | None = None
    last_log_username: str | None = None
    last_log_action: str | None = None
    last_log_timestamp: int | None = None
    log_history: list[dict[str, Any]] = field(default_factory=list)
    mode_change_history: list[dict[str, Any]] = field(default_factory=list)


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
    """Haelt eine dauerhafte Verbindung offen und pollt mehrere unabhaengige,
    getrennt konfigurierbare Ebenen (Zonen, Fehler, Alarmzustand, Ereignis-
    protokoll - je im festen Intervall; die umfangreiche Wireless-Abfrage nur
    einmal taeglich zu einer konfigurierbaren Uhrzeit oder manuell per Button).
    Ein gemeinsames Lock serialisiert alle Anfragen: ist das Geraet gerade mit
    einer anderen Ebene beschaeftigt, wartet der naechste Tick auf das Lock
    (Warteschlange der Tiefe 1 pro Ebene, da jede Ebene ihre eigene sequentielle
    Schleife ist) statt verworfen zu werden - eine Aktualisierung kann sich
    dadurch verzoegern, geht aber nie verloren."""

    _TIER_NAMES = ("zones", "faults", "mode", "extensive", "logs")
    _METRIC_WINDOW = 20
    _LOG_HISTORY_WINDOW = 20
    _MODE_CHANGE_HISTORY_WINDOW = 20
    # Kulanzfenster fuers nachtraegliche Zuordnen eines Log-Eintrags zu einem
    # erkannten Mode-Wechsel - grosszuegig genug fuer Logging-Verzug/leichte
    # Uhrzeitabweichung, aber eng genug, um zwei kurz aufeinanderfolgende,
    # unabhaengige Wechsel i.d.R. nicht zu verwechseln.
    _MODE_CHANGE_MATCH_WINDOW_S = 120

    def __init__(
        self,
        hass: HomeAssistant,
        api: SecvestApi,
        *,
        zones_interval_s: int,
        faults_interval_s: int,
        mode_interval_s: int,
        extensive_time: str,
        logs_interval_min: int,
        fetch_outputs: bool,
        wireless_enabled: bool,
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
        self._extensive_time = self._validate_time_str(extensive_time)
        self._logs_interval = max(60, int(logs_interval_min) * 60)
        self._fetch_outputs = fetch_outputs
        self.wireless_enabled = wireless_enabled
        self._reconnect_delay = max(1, int(reconnect_delay_s))

        self._last_log_timestamp: int | None = None
        # Behaelt jeden neu gesehenen Log-Eintrag mit Benutzerbezug, statt nur
        # den jeweils letzten zu ueberschreiben - damit bei zwei kurz
        # aufeinanderfolgenden Vorgaengen keiner verloren geht.
        self._log_history: deque[dict[str, Any]] = deque(maxlen=self._LOG_HISTORY_WINDOW)
        # Jeder erkannte Mode-Wechsel (gleich welcher Quelle - HA oder
        # Bedienteil), zunaechst ohne Benutzer; wird nachtraeglich mit einem
        # Log-Eintrag abgeglichen, sobald einer verfuegbar ist.
        self._mode_change_history: deque[dict[str, Any]] = deque(maxlen=self._MODE_CHANGE_HISTORY_WINDOW)

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

        # Diagnose: gleitende Antwortzeit und Wartezeit auf das Lock
        # (je letzte 20 Werte) pro Ebene
        self._response_times: dict[str, deque[float]] = {
            name: deque(maxlen=self._METRIC_WINDOW) for name in self._TIER_NAMES
        }
        self._wait_times: dict[str, deque[float]] = {
            name: deque(maxlen=self._METRIC_WINDOW) for name in self._TIER_NAMES
        }

    # -------------------------
    # Lifecycle
    # -------------------------

    def async_start(self) -> None:
        """Startet die unabhaengigen Poll-Loops. Die umfangreiche Abfrage laeuft
        nur, wenn Web-Credentials fuer RSSI/Wireless konfiguriert sind, und dann
        nur einmal taeglich zur konfigurierten Uhrzeit statt im festen Intervall
        - sie ist ein kompletter Web-UI-Login-Scrape und damit fuer die Anlage
        deutlich teurer als die uebrigen, leichtgewichtigen REST-Abfragen.
        RSSI/Batterie aendern sich ausserdem zu traege, um das zu rechtfertigen."""
        if self._tasks:
            return
        loops: list[tuple[str, Callable[[], Awaitable[None]], float]] = [
            ("zones", self._fetch_zones, self._zones_interval),
            ("faults", self._fetch_faults, self._faults_interval),
            ("mode", self._fetch_mode, self._mode_interval),
            ("logs", self._fetch_logs, self._logs_interval),
        ]

        for name, fetch_fn, interval_s in loops:
            self._tasks.append(
                self._create_task(self._loop(name, fetch_fn, interval_s), f"secvest_{name}_poll")
            )

        if self.wireless_enabled:
            self._tasks.append(
                self._create_task(
                    self._daily_loop("extensive", self._fetch_extensive, self._extensive_time),
                    "secvest_extensive_daily",
                )
            )

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
    # Scheduling: ein Request gleichzeitig, wartend statt verwerfend
    # -------------------------

    async def _loop(self, name: str, fetch_fn: Callable[[], Awaitable[None]], interval_s: float) -> None:
        while True:
            sleep_for = await self._run_tier(name, fetch_fn, interval_s)
            await asyncio.sleep(sleep_for)

    async def _run_tier(self, name: str, fetch_fn: Callable[[], Awaitable[None]], interval_s: float) -> float:
        now = time.time()
        if now < self._breaker_until:
            return max(1.0, min(interval_s, self._breaker_until - now))
        await self._run_once(name, fetch_fn)
        return interval_s

    async def _daily_loop(self, name: str, fetch_fn: Callable[[], Awaitable[None]], time_str: str) -> None:
        while True:
            await asyncio.sleep(self._seconds_until_next_daily_time(time_str))
            await self._run_once(name, fetch_fn)

    async def async_trigger_extensive(self) -> None:
        """Loest die umfangreiche Wireless-Abfrage sofort aus (Button), unabhaengig
        vom naechtlichen Zeitplan."""
        if not self.wireless_enabled:
            return
        await self._run_once("extensive", self._fetch_extensive)

    async def async_sync_logs_for_mode_change(self) -> None:
        """Fragt das Ereignisprotokoll nach einer Scharf-/Unscharfschaltung
        zeitnah ab, statt auf den naechsten planmaessigen Logs-Poll zu warten.
        Mit kurzen Wiederholungen, da die Firmware den Log-Eintrag nicht immer
        sofort bereitstellt. Die eigentliche Zuordnung zum richtigen Wechsel
        passiert unabhaengig davon in _try_match_pending_changes() - diese
        Methode beschleunigt nur, wann sie zum ersten Mal versucht wird."""
        for attempt in range(4):
            if attempt:
                await asyncio.sleep(1.5)
            found = await self._run_once("logs", self._fetch_logs)
            if found:
                return

    def _try_match_pending_changes(self) -> None:
        """Ordnet noch unbestaetigten Mode-Wechseln (_mode_change_history) den
        zeitlich naechsten noch nicht verwendeten Log-Eintrag mit Benutzerbezug
        zu (innerhalb von _MODE_CHANGE_MATCH_WINDOW_S). Laeuft nach jedem neu
        erkannten Wechsel und nach jedem Logs-Poll - funktioniert dadurch
        nachtraeglich fuer JEDEN erkannten Wechsel, unabhaengig davon, ob er
        ueber HA oder am Bedienteil ausgeloest wurde, und auch dann noch, wenn
        der passende Log-Eintrag erst bei einem spaeteren Poll eintrifft.
        Jeder Log-Eintrag wird dabei nur einem Wechsel zugeordnet (consumed)."""
        for change in self._mode_change_history:
            if change["user"] is not None:
                continue
            best: dict[str, Any] | None = None
            best_diff: float | None = None
            for log_entry in self._log_history:
                if log_entry["matched"]:
                    continue
                diff = abs(log_entry["timestamp"] - change["timestamp"])
                if diff > self._MODE_CHANGE_MATCH_WINDOW_S:
                    continue
                if best_diff is None or diff < best_diff:
                    best = log_entry
                    best_diff = diff
            if best is not None:
                change["user"] = best["user"]
                change["username"] = best["username"]
                change["action"] = best["action"]
                best["matched"] = True

    async def _run_once(self, name: str, fetch_fn: Callable[[], Awaitable[Any]]) -> Any:
        """Fuehrt einen einzelnen Abruf serialisiert ueber das gemeinsame Lock aus
        und pflegt Fehlerzaehler/Diagnose-Kennzahlen - gemeinsam genutzt von den
        Intervall-Loops, dem naechtlichen Zeitplan und dem manuellen Trigger.
        Gibt den Rueckgabewert von fetch_fn weiter (z. B. fuer die Logs-
        Zuordnung nach einer Schaltung), sonst None."""
        now = time.time()
        if now < self._breaker_until:
            return None

        # Ist das Lock gerade belegt, wartet dieser Aufruf hier - keine eigene
        # Warteschlange noetig, da jede Ebene ohnehin nur eine Schleife ist und
        # sich nie selbst ueberholen kann (max. ein Warter pro Ebene).
        wait_start = time.monotonic()
        async with self._request_lock:
            self._wait_times[name].append(time.monotonic() - wait_start)

            start = time.monotonic()
            try:
                result = await fetch_fn()
            except (asyncio.TimeoutError, SecvestApiError) as err:
                self._handle_failure(name, err)
                return None
            except Exception as err:  # unerwarteter Fehler: nicht die Loop sterben lassen
                _LOGGER.exception("Secvest %s-Abfrage fehlgeschlagen (unerwartet)", name)
                self._handle_failure(name, err)
                return None

            self._response_times[name].append(time.monotonic() - start)

        self._consecutive_failures = 0
        self._breaker_until = 0.0
        self._last_error = None
        return result

    @staticmethod
    def _validate_time_str(time_str: str) -> str:
        if re.match(r"^([01]\d|2[0-3]):[0-5]\d$", str(time_str or "")):
            return time_str
        _LOGGER.warning(
            "Secvest: ungueltige Uhrzeit fuer die umfangreiche Abfrage (%r), verwende Standard %s",
            time_str,
            DEFAULT_EXTENSIVE_TIME,
        )
        return DEFAULT_EXTENSIVE_TIME

    @staticmethod
    def _seconds_until_next_daily_time(time_str: str) -> float:
        hour, minute = (int(part) for part in time_str.split(":", 1))
        now = dt_util.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    # -------------------------
    # Diagnose-Kennzahlen (siehe sensor.py)
    # -------------------------

    def average_response_time_ms(self, name: str) -> float | None:
        """Gleitender Durchschnitt (letzte 20 Werte) der reinen API-Antwortzeit
        einer Ebene in ms - ohne Wartezeit auf das gemeinsame Lock."""
        times = self._response_times.get(name)
        if not times:
            return None
        return round(sum(times) / len(times) * 1000, 1)

    def average_wait_time_ms(self, name: str) -> float | None:
        """Gleitender Durchschnitt (letzte 20 Werte) der Wartezeit einer Ebene
        auf das gemeinsame Lock in ms - hoch bedeutet haeufige Kollisionen
        mit anderen Ebenen, nicht zwangsweise ein Problem der Secvest selbst."""
        times = self._wait_times.get(name)
        if not times:
            return None
        return round(sum(times) / len(times) * 1000, 1)

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

        update_kwargs: dict[str, Any] = {}
        # Jeder erkannte Wechsel wird registriert, gleich ob er ueber HA oder
        # am Bedienteil ausgeloest wurde - die Benutzer-Zuordnung passiert
        # nachtraeglich in _try_match_pending_changes(), sobald ein Log-Eintrag
        # dazu verfuegbar ist (auch rueckwirkend bei einem spaeteren Poll).
        if base.raw_mode is not None and base.raw_mode != raw_mode:
            self._mode_change_history.append(
                {
                    "timestamp": time.time(),
                    "from_mode": base.raw_mode,
                    "to_mode": raw_mode,
                    "user": None,
                    "username": None,
                    "action": None,
                }
            )
            self._try_match_pending_changes()
            update_kwargs["mode_change_history"] = list(self._mode_change_history)

        self.async_set_updated_data(
            replace(
                base,
                raw_mode=raw_mode,
                human_mode=human_mode,
                available=True,
                last_error=None,
                **update_kwargs,
            )
        )

    async def _fetch_extensive(self) -> None:
        # Liefert RSSI/Batterie/Sabotage je Zonen-Index; wird von der naechsten
        # Zonen-Abfrage automatisch mit uebernommen (kein eigener Zonen-Request hier).
        self._wireless_cache = await self.api.get_wireless_zones_status()
        base = self._base_data()
        self.async_set_updated_data(replace(base, available=True, last_error=None))

    async def _fetch_logs(self) -> bool:
        # Liefert nur neue Eintraege seit dem letzten erfolgreich verarbeiteten
        # Zeitstempel ("ge" = >=, daher +1 um den letzten Eintrag nicht erneut
        # zu bekommen). Beim allerersten Aufruf (since=None) liefert die
        # Firmware bis zu 600 Eintraege (komplette Logkapazitaet) - entsprechend
        # langsamer, aber nur einmalig.
        since = self._last_log_timestamp + 1 if self._last_log_timestamp is not None else None
        entries = await self.api.get_logs(since=since)

        newest_timestamp = self._last_log_timestamp
        # Jeder neue Eintrag mit Benutzerbezug wird an die Historie angehaengt
        # (nicht nur der letzte ueberschrieben) - bei zwei kurz
        # aufeinanderfolgenden Vorgaengen geht so keiner verloren.
        new_user_entries: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        for entry in entries:
            for event in entry.get("events", []):
                try:
                    ts = int(event.get("timestamp"))
                except (TypeError, ValueError):
                    continue
                if newest_timestamp is None or ts > newest_timestamp:
                    newest_timestamp = ts
                if event.get("user") is None:
                    continue
                new_user_entries.append((ts, entry, event))

        if newest_timestamp is not None:
            self._last_log_timestamp = newest_timestamp

        new_user_entries.sort(key=lambda item: item[0])
        for ts, entry, event in new_user_entries:
            self._log_history.append(
                {
                    "timestamp": ts,
                    "user": str(event.get("user")),
                    "username": event.get("username"),
                    "action": entry.get("desc"),
                    "matched": False,
                }
            )

        update_kwargs: dict[str, Any] = {"log_history": list(self._log_history)}
        if new_user_entries:
            last = self._log_history[-1]
            update_kwargs.update(
                last_log_user=last["user"],
                last_log_username=last["username"],
                last_log_action=last["action"],
                last_log_timestamp=last["timestamp"],
            )
            # Neue Eintraege koennen zu laengst erkannten, noch unbestaetigten
            # Mode-Wechseln gehoeren (auch ueber mehrere Logs-Polls hinweg
            # nachtraeglich) - deshalb nach jedem Fetch neu versuchen.
            self._try_match_pending_changes()
            update_kwargs["mode_change_history"] = list(self._mode_change_history)

        base = self._base_data()
        self.async_set_updated_data(replace(base, available=True, last_error=None, **update_kwargs))
        return bool(new_user_entries)
