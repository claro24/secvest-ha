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
VALID_MODES = {MODE_SET, MODE_PARTSET, MODE_UNSET}

STATE_TRANSLATIONS = {
    MODE_SET: "Scharf",
    MODE_PARTSET: "Teilscharf",
    MODE_UNSET: "Unscharf",
}
