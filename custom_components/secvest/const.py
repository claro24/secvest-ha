DOMAIN = "secvest"

CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_USER_CODE = "user_code"
CONF_WEB_USERNAME = "web_username"
CONF_WEB_PASSWORD = "web_password"
CONF_VERIFY_SSL = "verify_ssl"

# Vier unabhaengige Abfrage-Ebenen, je eigenes Intervall
CONF_ZONES_INTERVAL = "zones_interval"          # Sekunden
CONF_FAULTS_INTERVAL = "faults_interval"        # Sekunden
CONF_MODE_INTERVAL = "mode_interval"            # Sekunden (Alarmzustand)
CONF_EXTENSIVE_INTERVAL = "extensive_interval"  # Minuten (Wireless/RSSI-Scrape)
CONF_FETCH_OUTPUTS = "fetch_outputs"            # Ausgaenge im Zonen-Takt mitfragen
CONF_RECONNECT_DELAY = "reconnect_delay"        # Sekunden Wartezeit nach Verbindungsabbruch

CONF_RETRIES = "retries"
CONF_BREAKER_THRESHOLD = "breaker_threshold"
CONF_BREAKER_COOLDOWN = "breaker_cooldown"

LOGGER_NAME = "custom_components.secvest"

DEFAULT_VERIFY_SSL = False
DEFAULT_ZONES_INTERVAL = 3        # Sekunden
DEFAULT_FAULTS_INTERVAL = 3       # Sekunden
DEFAULT_MODE_INTERVAL = 5         # Sekunden
DEFAULT_EXTENSIVE_INTERVAL = 5    # Minuten
DEFAULT_FETCH_OUTPUTS = True
DEFAULT_RECONNECT_DELAY = 10      # Sekunden, schneller Reconnect-Versuch
DEFAULT_RETRIES = 4
DEFAULT_BREAKER_THRESHOLD = 5      # nach 5 Fehlversuchen -> lange Pause
DEFAULT_BREAKER_COOLDOWN = 300      # Sekunden (5 Minuten)

SERVICE_SET_MODE = "secvest_set_mode"
SERVICE_DUMP_DIAGNOSTICS = "dump_diagnostics"

MODE_SET = "set"
MODE_PARTSET = "partset"
MODE_UNSET = "unset"

# Zusaetzliche Rohzustaende, die die Firmware bei einem ausgeloesten Alarm
# liefert (abgeglichen mit dem Secvest-Logbuch waehrend eines echten Alarms).
# partset-alarm wurde nicht beobachtet, ist aber analog zu set-alarm zu
# erwarten. Fuer die Ausgangsverzoegerung gibt es bisher keinen eigenen
# Rohwert (beobachtet wurde nur unset -> set).
MODE_UNSET_ALARM = "unset-alarm"
MODE_SET_ALARM = "set-alarm"
MODE_PARTSET_ALARM = "partset-alarm"
MODE_ACKNOWLEDGED = "acknowledged"

# Nur diese drei sind gueltige Ziele fuer PUT /system/partitions-1/ bzw. den
# Service secvest_set_mode - die Alarm-/Acknowledged-Rohwerte liefert die
# Firmware nur, sie lassen sich nicht direkt anfordern.
VALID_MODES = {MODE_SET, MODE_PARTSET, MODE_UNSET}

# HA-AlarmControlPanelState-Werte als reine Strings, damit const.py auch ohne
# installiertes homeassistant-Paket importierbar und testbar bleibt.
_HA_STATE_DISARMED = "disarmed"
_HA_STATE_ARMED_HOME = "armed_home"
_HA_STATE_ARMED_AWAY = "armed_away"
_HA_STATE_PENDING = "pending"
_HA_STATE_TRIGGERED = "triggered"

# Rohzustand -> AlarmControlPanelState (als String-Wert, siehe oben)
RAW_TO_HA_STATE = {
    MODE_UNSET: _HA_STATE_DISARMED,
    MODE_PARTSET: _HA_STATE_ARMED_HOME,
    MODE_SET: _HA_STATE_ARMED_AWAY,
    MODE_UNSET_ALARM: _HA_STATE_PENDING,
    MODE_SET_ALARM: _HA_STATE_TRIGGERED,
    MODE_PARTSET_ALARM: _HA_STATE_TRIGGERED,
    MODE_ACKNOWLEDGED: _HA_STATE_DISARMED,
}

# Rohzustaende, bei denen ein Alarm ausgeloest und noch nicht quittiert/
# zurueckgesetzt wurde (fuer das extra_state_attribute "alarm_memory")
ALARM_MEMORY_MODES = {
    MODE_UNSET_ALARM,
    MODE_SET_ALARM,
    MODE_PARTSET_ALARM,
    MODE_ACKNOWLEDGED,
}

STATE_TRANSLATIONS = {
    MODE_SET: "Scharf",
    MODE_PARTSET: "Teilscharf",
    MODE_UNSET: "Unscharf",
    MODE_UNSET_ALARM: "Eingangsverzögerung",
    MODE_SET_ALARM: "Alarm",
    MODE_PARTSET_ALARM: "Alarm (teilscharf)",
    MODE_ACKNOWLEDGED: "Unscharf (Alarm nicht quittiert)",
}
