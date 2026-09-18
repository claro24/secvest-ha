from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse
from xml.etree import ElementTree
from .const import DEFAULT_RETRIES

import aiohttp


# Bei deiner Secvest besser großzügig sein:
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=20)

DEFAULT_BACKOFF_BASE_S = 1.2   # 1.2s, 2.4s, 4.8s, ...
DEFAULT_JITTER_S = 0.4         # +0..0.4s zufällig


@dataclass
class SecvestAuth:
    username: str
    password: str
    user_code: str
    web_username: str | None = None
    web_password: str | None = None


class SecvestApiError(Exception):
    """Base error for Secvest API."""


class SecvestApi:
    """
    Robust Secvest REST client:
    - Retries with backoff on timeouts / connection issues / 5xx
    - 404 fallback for endpoints that are slash-sensitive
    - Always parses JSON from text (Secvest sometimes sends wrong content-type)
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        auth: SecvestAuth,
        verify_ssl: bool,
        timeout: aiohttp.ClientTimeout = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self._session = session
        self._host = host.rstrip("/")
        self._auth = auth
        self._verify_ssl = verify_ssl
        self._timeout = timeout
        self._retries = retries
        self._web_ssid: str | None = None
        self._web_cookie_header: str | None = None
        self._web_csrf_token: str | None = None
        self._web_lock = asyncio.Lock()
        self._wireless_cache: dict[int, dict[str, Any]] = {}
        self._wireless_debug: dict[str, Any] = {}
        self._wireless_last_error: str | None = None

    def _url(self, path: str) -> str:
        return f"{self._host}{path}"

    def _ssl(self) -> bool:
        # verify_ssl=False => ssl=False (keine Zertifikatsprüfung)
        return True if self._verify_ssl else False

    def _auth_basic(self) -> aiohttp.BasicAuth:
        return aiohttp.BasicAuth(self._auth.username, self._auth.password)

    def _common_headers(self) -> dict[str, str]:
        # Verbindung bewusst offen halten (Keep-Alive): der TLS-Handshake
        # dieser Embedded-API dauert ~6-7s, eine wiederverwendete Verbindung
        # dagegen <100ms. Reconnect nach Abbruch uebernimmt der Coordinator.
        return {"Accept": "application/json"}

    async def _read_json(self, resp: aiohttp.ClientResponse) -> Any:
        # Robust: immer Text lesen und selbst JSON parsen
        text = (await resp.text()).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise SecvestApiError(f"Response not JSON (status={resp.status}) body={text[:200]}") from e

    async def _request_json(
        self,
        method: str,
        paths: Iterable[str],
        *,
        json_payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expect_json: bool = True,
    ) -> Any:
        """
        Try request against one or multiple alternative paths (fallback).
        Retries on transient errors.
        """
        last_exc: Exception | None = None
        paths_list = list(paths)

        for attempt in range(1, self._retries + 1):
            for path in paths_list:
                url = self._url(path)
                try:
                    async with self._session.request(
                        method,
                        url,
                        json=json_payload,
                        auth=self._auth_basic(),
                        ssl=self._ssl(),
                        timeout=self._timeout,
                        headers=headers or self._common_headers(),
                    ) as resp:

                        # 404: probiere nächsten Path (Slash/No-Slash Fallback)
                        if resp.status == 404 and len(paths_list) > 1:
                            continue

                        # Auth-Fehler nicht retryen
                        if resp.status in (401, 403):
                            raise SecvestApiError(f"Auth failed ({resp.status}) for {url}")

                        # 5xx retryen
                        if 500 <= resp.status <= 599:
                            raise aiohttp.ClientResponseError(
                                request_info=resp.request_info,
                                history=resp.history,
                                status=resp.status,
                                message=f"Server error {resp.status}",
                                headers=resp.headers,
                            )

                        # Sonstige 4xx (außer 404-fallback) -> hart
                        resp.raise_for_status()

                        if not expect_json:
                            return None

                        return await self._read_json(resp)

                except (asyncio.TimeoutError, aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError,
                        aiohttp.ClientOSError, aiohttp.ClientPayloadError, aiohttp.ClientResponseError) as e:
                    last_exc = e
                    # Retry nach Backoff
                    await asyncio.sleep(self._backoff(attempt))
                    break  # nächster attempt (nicht alle paths sinnlos weiterprobieren)
                except SecvestApiError as e:
                    # Auth/Non-JSON -> hart, kein Retry
                    raise
                except Exception as e:
                    # Unknown -> hart
                    raise SecvestApiError(f"Unexpected error calling {url}: {e!r}") from e

        raise SecvestApiError(f"Request failed after retries: {last_exc!r}") from last_exc

    def _backoff(self, attempt: int) -> float:
        # Exponentieller Backoff + jitter
        base = DEFAULT_BACKOFF_BASE_S * (2 ** (attempt - 1))
        jitter = random.random() * DEFAULT_JITTER_S
        return base + jitter

    async def _request_text(
        self,
        method: str,
        paths: Iterable[str],
        *,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        last_exc: Exception | None = None
        paths_list = list(paths)

        for attempt in range(1, self._retries + 1):
            for path in paths_list:
                url = self._url(path)
                try:
                    async with self._session.request(
                        method,
                        url,
                        data=data,
                        auth=self._auth_basic(),
                        ssl=self._ssl(),
                        timeout=self._timeout,
                        headers=headers or self._common_headers(),
                    ) as resp:
                        if resp.status == 404 and len(paths_list) > 1:
                            continue
                        if resp.status in (401, 403):
                            raise SecvestApiError(f"Auth failed ({resp.status}) for {url}")
                        if 500 <= resp.status <= 599:
                            raise aiohttp.ClientResponseError(
                                request_info=resp.request_info,
                                history=resp.history,
                                status=resp.status,
                                message=f"Server error {resp.status}",
                                headers=resp.headers,
                            )
                        resp.raise_for_status()
                        return await resp.text()

                except (
                    asyncio.TimeoutError,
                    aiohttp.ClientConnectorError,
                    aiohttp.ServerDisconnectedError,
                    aiohttp.ClientOSError,
                    aiohttp.ClientPayloadError,
                    aiohttp.ClientResponseError,
                ) as e:
                    last_exc = e
                    await asyncio.sleep(self._backoff(attempt))
                    break
                except SecvestApiError:
                    raise
                except Exception as e:
                    raise SecvestApiError(f"Unexpected error calling {url}: {e!r}") from e

        raise SecvestApiError(f"Request failed after retries: {last_exc!r}") from last_exc

    async def _request_text_with_cookies(
        self,
        method: str,
        paths: Iterable[str],
        *,
        data: Any = None,
        headers: dict[str, str] | None = None,
        use_auth: bool = True,
        allow_statuses: set[int] | None = None,
    ) -> tuple[str, str]:
        last_exc: Exception | None = None
        paths_list = list(paths)
        allow_statuses = allow_statuses or set()

        for attempt in range(1, self._retries + 1):
            for path in paths_list:
                url = self._url(path)
                try:
                    async with self._session.request(
                        method,
                        url,
                        data=data,
                        auth=self._auth_basic() if use_auth else None,
                        ssl=self._ssl(),
                        timeout=self._timeout,
                        headers=headers or self._common_headers(),
                    ) as resp:
                        text = await resp.text()
                        cookie_header = "; ".join(
                            f"{name}={cookie.value}" for name, cookie in resp.cookies.items()
                        )
                        if resp.status in allow_statuses:
                            return text, cookie_header
                        if resp.status == 404 and len(paths_list) > 1:
                            continue
                        if resp.status in (401, 403):
                            raise SecvestApiError(f"Auth failed ({resp.status}) for {url}")
                        if 500 <= resp.status <= 599:
                            raise aiohttp.ClientResponseError(
                                request_info=resp.request_info,
                                history=resp.history,
                                status=resp.status,
                                message=f"Server error {resp.status}",
                                headers=resp.headers,
                            )
                        resp.raise_for_status()
                        return text, cookie_header

                except (
                    asyncio.TimeoutError,
                    aiohttp.ClientConnectorError,
                    aiohttp.ServerDisconnectedError,
                    aiohttp.ClientOSError,
                    aiohttp.ClientPayloadError,
                    aiohttp.ClientResponseError,
                ) as e:
                    last_exc = e
                    await asyncio.sleep(self._backoff(attempt))
                    break
                except SecvestApiError:
                    raise
                except Exception as e:
                    raise SecvestApiError(f"Unexpected error calling {url}: {e!r}") from e

        raise SecvestApiError(f"Request failed after retries: {last_exc!r}") from last_exc

    @staticmethod
    def _is_visible(value: str | None) -> bool:
        return str(value or "").strip().lower() not in {"", "none", "hidden"}

    @staticmethod
    def _parse_rssi(value: str | None) -> tuple[int | None, int | None]:
        if not value:
            return None, None
        match = re.search(r"(\d+)\s*(?:<br\s*/?>)?\s*\((\d+)\)", value)
        if not match:
            match = re.search(r"(\d+)", value)
        if not match:
            return None, None
        current = int(match.group(1))
        previous = int(match.group(2)) if match.lastindex and match.lastindex >= 2 else None
        return current, previous

    @classmethod
    def _parse_wireless_zones_form(cls, text: str) -> dict[int, dict[str, Any]]:
        root = ElementTree.fromstring(text.strip())
        values: dict[str, str] = {}

        for node in root:
            node_id = node.findtext("id")
            value = node.findtext("value")
            if node_id:
                values[node_id] = value or ""

        indexes: set[int] = set()
        for key in values:
            match = re.match(r"rtm\d+_(\d+)$", key)
            if match:
                indexes.add(int(match.group(1)))

        result: dict[int, dict[str, Any]] = {}
        for idx in sorted(indexes):
            rssi_text = values.get(f"rtm7_{idx}") or values.get(f"rtm2_{idx}") or ""
            rssi_current, rssi_previous = cls._parse_rssi(rssi_text)
            bargraph_raw = values.get(f"rtm1_{idx}")
            try:
                rssi_bargraph = int(bargraph_raw) if bargraph_raw not in (None, "") else None
            except ValueError:
                rssi_bargraph = None

            if rssi_current is None and rssi_bargraph is None:
                continue

            result[idx] = {
                "rssi": rssi_text.replace("<br/>", " ").strip(),
                "rssi_current": rssi_current,
                "rssi_previous": rssi_previous,
                "rssi_bargraph": rssi_bargraph,
                "web_battery_low": cls._is_visible(values.get(f"rtm8_{idx}")),
                "web_omitted": cls._is_visible(values.get(f"rtm5_{idx}")),
                "web_supervision_fault": cls._is_visible(values.get(f"rtm11_{idx}")),
                "web_sabotage": cls._is_visible(values.get(f"rtm9_{idx}")),
                "web_open": cls._is_visible(values.get(f"rtm10_{idx}")),
                "web_raw": {
                    "rtm1": values.get(f"rtm1_{idx}"),
                    "rtm2": values.get(f"rtm2_{idx}"),
                    "rtm5": values.get(f"rtm5_{idx}"),
                    "rtm6": values.get(f"rtm6_{idx}"),
                    "rtm7": values.get(f"rtm7_{idx}"),
                    "rtm8": values.get(f"rtm8_{idx}"),
                    "rtm9": values.get(f"rtm9_{idx}"),
                    "rtm10": values.get(f"rtm10_{idx}"),
                    "rtm11": values.get(f"rtm11_{idx}"),
                },
            }
        return result

    # -------------------------
    # Public API
    # -------------------------

    async def get_mode(self) -> str:
        # Deine Secvest: state OHNE trailing slash (slash liefert 404)
        data = await self._request_json(
            "GET",
            paths=(
                "/system/partitions-1/state",   # korrekt bei dir
                "/system/partitions-1/state/",  # fallback falls andere Firmware
            ),
        )
        state = data.get("state") if isinstance(data, dict) else None
        if not isinstance(state, str):
            raise SecvestApiError(f"Invalid mode payload: {data!r}")
        return state

    async def get_zones(self) -> list[dict[str, Any]]:
        # Zones bei Secvest meistens mit trailing slash
        data = await self._request_json(
            "GET",
            paths=(
                "/system/partitions-1/zones/",
                "/system/partitions-1/zones",
            ),
        )
        if not isinstance(data, list):
            raise SecvestApiError(f"Invalid zones payload: {data!r}")
        return data

    async def get_faults(self) -> list[dict[str, Any]]:
        data = await self._request_json(
            "GET",
            paths=(
                "/faults/",
                "/faults",
            ),
        )
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict) and isinstance(data.get("faults"), list):
            return [item for item in data["faults"] if isinstance(item, dict)]
        raise SecvestApiError(f"Invalid faults payload: {data!r}")

    async def get_outputs(self) -> list[dict[str, Any]]:
        data = await self._request_json(
            "GET",
            paths=(
                "/output/",
                "/output",
                "/outputs/",
                "/outputs",
            ),
        )
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict) and isinstance(data.get("outputs"), list):
            return [item for item in data["outputs"] if isinstance(item, dict)]
        raise SecvestApiError(f"Invalid outputs payload: {data!r}")

    async def get_wireless_zones_status(self) -> dict[int, dict[str, Any]]:
        if not self._auth.web_username or not self._auth.web_password:
            return {}
        async with self._web_lock:
            try:
                text, debug = await self._get_wireless_zones_form_probe()
                parsed = self._parse_wireless_zones_form(text)
                if parsed:
                    self._wireless_cache = parsed
                self._wireless_debug = debug
                self._wireless_last_error = None
            except Exception as err:
                self._wireless_last_error = repr(err)
                if not self._wireless_cache:
                    raise
            return dict(self._wireless_cache)

    async def get_wireless_zones_status_debug(self) -> dict[str, Any]:
        if not self._auth.web_username or not self._auth.web_password:
            return {
                "enabled": False,
                "reason": "Separate web credentials are not configured",
            }
        debug = dict(self._wireless_debug)
        debug.update(
            {
                "enabled": True,
                "cached_count": len(self._wireless_cache),
                "last_error": self._wireless_last_error,
                "parsed_preview": dict(list(self._wireless_cache.items())[:3]),
            }
        )
        return debug

    @staticmethod
    def _extract_csrf_token(text: str) -> str | None:
        patterns = (
            r"\bCSRF_TOKEN\s*=\s*([-0-9]+)",
            r'"csrf_token"\s*:\s*"([^"]+)"',
            r"'csrf_token'\s*:\s*'([^']+)'",
            r"name=['\"]csrf_token['\"]\s+value=['\"]([^'\"]+)['\"]",
            r"csrf_token=([-0-9]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _extract_ssid(text: str) -> str | None:
        patterns = (
            r"value=['\"]([0-9A-Za-z_-]+)['\"]\s+id=['\"]ssid['\"]",
            r"id=['\"]ssid['\"]\s+value=['\"]([0-9A-Za-z_-]+)['\"]",
            r"ssid=([0-9A-Za-z_-]+)",
            r"ssid['\"]?\s*[:=]\s*['\"]([0-9A-Za-z_-]+)['\"]",
            r"document\.cookie\s*=\s*['\"]ssid=([0-9A-Za-z_-]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _wireless_zones_payload(csrf_token: str | None = None) -> dict[str, str]:
        payload = {
            "bus": "0",
            "busdevice": "0",
            "edit": "0",
            "editSecondary": "0",
            "panelOutputsArePlugby": "0",
            "listingType": "busDevice",
            "listingIOMod": "0",
            "table_start_index": "0",
            "table_range": "10",
            "contact_index": "0",
        }
        if csrf_token is not None:
            payload["csrf_token"] = csrf_token
        return payload

    @staticmethod
    def _cookie_debug(cookie_header: str) -> dict[str, Any]:
        parts = [part.strip() for part in cookie_header.split(";") if part.strip()]
        names = [part.split("=", 1)[0] for part in parts if "=" in part]
        return {
            "present": bool(parts),
            "names": names,
            "length": len(cookie_header),
        }

    def _get_or_new_ssid(self) -> str:
        if not self._web_ssid:
            self._web_ssid = str(random.randint(1_000_000_000, 2_147_483_647))
        return self._web_ssid

    async def _get_wireless_zones_form_probe(self) -> tuple[str, dict[str, Any]]:
        parsed_host = urlparse(self._host)
        origin = f"{parsed_host.scheme}://{parsed_host.netloc}" if parsed_host.scheme and parsed_host.netloc else self._host
        debug: dict[str, Any] = {"attempts": []}

        if self._web_cookie_header and self._web_csrf_token:
            reused_headers = {
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": origin,
                "Referer": f"{self._host}/sec_main.cgi",
                "X-Requested-With": "XMLHttpRequest",
                "Cookie": self._web_cookie_header,
            }
            reused_text, _reused_cookie = await self._request_text_with_cookies(
                "POST",
                paths=(
                    "/sec_zones.cgx",
                    "sec_zones.cgx",
                ),
                data=self._wireless_zones_payload(self._web_csrf_token),
                headers=reused_headers,
                use_auth=False,
                allow_statuses={401, 403},
            )
            debug["session_reuse"] = {
                "response_length": len(reused_text),
                "has_rtm": "rtm" in reused_text,
                "has_nloggedin": "<nloggedin>" in reused_text,
                "response_preview": reused_text[:300],
            }
            if "rtm" in reused_text:
                return reused_text, debug
            self._web_cookie_header = None
            self._web_csrf_token = None

        cookie_ssid = self._get_or_new_ssid()
        cookie_header = f"ssid={cookie_ssid}"

        xhr_headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": origin,
            "Referer": f"{self._host}/sec_main.cgi",
            "X-Requested-With": "XMLHttpRequest",
            "Cookie": cookie_header,
        }

        login_text, login_cookie_header = await self._request_text_with_cookies(
            "POST",
            paths=(
                "/sec_login.cgi",
                "sec_login.cgi",
            ),
            data={
                "usr": self._auth.web_username,
                "pwd": self._auth.web_password,
            },
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": origin,
                "Referer": f"{self._host}/sec_login.cgi",
                "Cookie": cookie_header,
            },
            use_auth=False,
        )
        login_ssid = self._extract_ssid(login_text)
        debug["sec_login"] = {
            "response_length": len(login_text),
            "response_preview": login_text[:500],
            "cookie": self._cookie_debug(login_cookie_header),
            "session_id_found": login_ssid is not None,
        }
        if not login_ssid:
            raise SecvestApiError("Web login did not return a session id")
        cookie_header = f"ssid={login_ssid}"
        self._web_cookie_header = cookie_header
        xhr_headers = dict(xhr_headers)
        xhr_headers["Cookie"] = cookie_header

        main_text, main_cookie_header = await self._request_text_with_cookies(
            "POST",
            paths=(
                "/sec_main.cgi",
                "sec_main.cgi",
            ),
            data={"ssid": login_ssid},
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": origin,
                "Referer": f"{self._host}/sec_login.cgi",
                "Cookie": cookie_header,
            },
            use_auth=False,
        )
        debug["sec_main"] = {
            "response_length": len(main_text),
            "response_preview": main_text[:500],
            "cookie": self._cookie_debug(main_cookie_header),
            "session_active": "<nloggedin>" not in main_text,
        }

        dynamic_headers = {
            "Accept": "*/*",
            "Referer": f"{self._host}/sec_main.cgi",
            "Cookie": cookie_header,
        }
        dynamic_text, dynamic_cookie_header = await self._request_text_with_cookies(
            "GET",
            paths=(
                "/sec_dynamicjs.cgi",
                "sec_dynamicjs.cgi",
            ),
            headers=dynamic_headers,
            use_auth=False,
        )
        csrf_token = self._extract_csrf_token(dynamic_text)
        debug["sec_dynamicjs"] = {
            "response_length": len(dynamic_text),
            "response_preview": dynamic_text[:500],
            "cookie": self._cookie_debug(dynamic_cookie_header),
            "csrf_token_found": csrf_token is not None,
        }
        if not csrf_token:
            raise SecvestApiError("Web session did not return a CSRF token")
        self._web_csrf_token = csrf_token

        welcome_payload = self._wireless_zones_payload(csrf_token)
        welcome_payload["bus"] = "1"
        welcome_text, welcome_cookie_header = await self._request_text_with_cookies(
            "POST",
            paths=(
                "/sec_welcome.cgi",
                "sec_welcome.cgi",
            ),
            data=welcome_payload,
            headers=xhr_headers,
            use_auth=False,
        )
        debug["sec_welcome_cgi"] = {
            "response_length": len(welcome_text),
            "response_preview": welcome_text[:500],
            "cookie": self._cookie_debug(welcome_cookie_header),
            "has_nloggedin": "<nloggedin>" in welcome_text,
        }

        welcome_status_text, welcome_status_cookie_header = await self._request_text_with_cookies(
            "POST",
            paths=(
                "/sec_welcome.cgx",
                "sec_welcome.cgx",
            ),
            data=welcome_payload,
            headers=xhr_headers,
            use_auth=False,
        )
        debug["sec_welcome_cgx"] = {
            "response_length": len(welcome_status_text),
            "response_preview": welcome_status_text[:500],
            "cookie": self._cookie_debug(welcome_status_cookie_header),
            "has_nloggedin": "<nloggedin>" in welcome_status_text,
        }

        zones_payload = self._wireless_zones_payload(csrf_token)
        cgi_text, cgi_cookie_header = await self._request_text_with_cookies(
            "POST",
            paths=(
                "/sec_zones.cgi",
                "sec_zones.cgi",
            ),
            data=zones_payload,
            headers=xhr_headers,
            use_auth=False,
        )
        debug["sec_zones_cgi"] = {
            "response_length": len(cgi_text),
            "response_preview": cgi_text[:500],
            "cookie": self._cookie_debug(cgi_cookie_header),
            "has_nloggedin": "<nloggedin>" in cgi_text,
            "has_rtm": "rtm" in cgi_text,
        }

        text, status_cookie_header = await self._request_text_with_cookies(
            "POST",
            paths=(
                "/sec_zones.cgx",
                "sec_zones.cgx",
            ),
            data=zones_payload,
            headers=xhr_headers,
            use_auth=False,
        )
        debug["attempts"].append(
            {
                "label": "authenticated_web_session",
                "payload_keys": list(zones_payload.keys()),
                "response_length": len(text),
                "has_rtm": "rtm" in text,
                "has_nloggedin": "<nloggedin>" in text,
                "cookie": self._cookie_debug(status_cookie_header),
                "response_preview": text[:300],
            }
        )
        return text, debug

    async def ack_fault(self, fault_id: str) -> None:
        await self._request_json(
            "PUT",
            paths=(
                f"/faults/{fault_id}/",
                f"/faults/{fault_id}",
            ),
            json_payload={"ack": True, "acknowledge": True},
            headers={"Content-Type": "application/json"},
            expect_json=False,
        )

    async def set_mode(self, new_state: str) -> None:
        # PUT endpoint hat bei dir Slash am Ende
        await self._request_json(
            "PUT",
            paths=(
                "/system/partitions-1/",
                "/system/partitions-1",
            ),
            json_payload={"state": new_state, "code": self._auth.user_code},
            headers={"Content-Type": "application/json"},
            expect_json=False,
        )
