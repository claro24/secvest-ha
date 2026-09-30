"""Tests fuer das Rohzustand->AlarmControlPanelState-Mapping in const.py.

const.py importiert bewusst kein homeassistant-Paket, damit dieser Test
auch ohne installierte HA-Umgebung laeuft. Das Modul wird deshalb per
Dateipfad geladen statt ueber den normalen Paket-Import (der wuerde
custom_components/secvest/__init__.py ausfuehren, welches homeassistant
importiert).
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_CONST_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "secvest"
    / "const.py"
)


def _load_const():
    spec = importlib.util.spec_from_file_location("secvest_const", _CONST_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


const = _load_const()


@pytest.mark.parametrize(
    ("raw", "expected_ha_state"),
    [
        (const.MODE_UNSET, "disarmed"),
        (const.MODE_PARTSET, "armed_home"),
        (const.MODE_SET, "armed_away"),
        (const.MODE_UNSET_ALARM, "pending"),
        (const.MODE_SET_ALARM, "triggered"),
        (const.MODE_PARTSET_ALARM, "triggered"),
        (const.MODE_ACKNOWLEDGED, "disarmed"),
    ],
)
def test_raw_to_ha_state_mapping(raw: str, expected_ha_state: str) -> None:
    assert const.RAW_TO_HA_STATE[raw] == expected_ha_state


def test_unknown_raw_value_is_not_mapped() -> None:
    assert const.RAW_TO_HA_STATE.get("some-future-firmware-value") is None


def test_state_translations_cover_every_mapped_raw_mode() -> None:
    for raw in const.RAW_TO_HA_STATE:
        assert raw in const.STATE_TRANSLATIONS
        assert const.STATE_TRANSLATIONS[raw]


def test_valid_modes_unchanged() -> None:
    # Nur diese drei duerfen ueber PUT/den Service secvest_set_mode angefordert werden
    assert const.VALID_MODES == {"set", "partset", "unset"}


def test_alarm_memory_modes() -> None:
    assert const.ALARM_MEMORY_MODES == {
        const.MODE_UNSET_ALARM,
        const.MODE_SET_ALARM,
        const.MODE_PARTSET_ALARM,
        const.MODE_ACKNOWLEDGED,
    }
    # Die drei normalen Betriebszustaende sind kein "Alarm im Speicher"
    assert const.MODE_UNSET not in const.ALARM_MEMORY_MODES
    assert const.MODE_PARTSET not in const.ALARM_MEMORY_MODES
    assert const.MODE_SET not in const.ALARM_MEMORY_MODES
