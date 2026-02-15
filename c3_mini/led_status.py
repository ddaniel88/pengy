# led_status.py (NeoPixel on ESP32-S3, startup white until first measurement)
import machine, neopixel
import time

PIN = 48 # C3 - pin 7; S3 - pin 48
NUM = 1

BRIGHT = 45
X_BRIGHT = 150

np = neopixel.NeoPixel(machine.Pin(PIN), NUM)

# osnovne boje (r, g, b)
COLOR_BOOT = (180, 120, 0)           # žućkasto kad se diže
COLOR_OK = (0, BRIGHT, 0)            # zeleno kad je online
COLOR_WIFI_FAIL = (220, 0, 0)        # crveno kad nije online

# STARTUP / pre prvog merenja
COLOR_STARTUP_WHITE = (0, 0, 80)

# merenje = NE DIRAMO više (da bela ne stoji 30+ sekundi na SEN55)
# ostavljamo definiciju čisto da postoji ako ti zatreba kasnije
COLOR_MEASURING = (80, 80, 80)

# uploading boja ostaje definisana, ali je više nećemo koristiti
COLOR_UPLOADING = (0, 60, 180)

# AQI boje (sens.comm stil / 6 nivoa)
# 0 good, 1 fair, 2 moderate, 3 poor, 4 very poor, 5 extremely poor
AQI_COLORS = [
    (0, 200, 120),       # good - tirkizno
    (0, 160, 40),        # fair - zelenkasto
    (200, 180, 0),       # moderate - žućkasto
    (X_BRIGHT, 27, 0),   # 3 - orange
    (X_BRIGHT, 0, 0),    # 4 - crveno
    (X_BRIGHT, 0, 90),   # 5 - ljubičasto
]

_current = (0, 0, 0)
_stable_color = None

current_mode = "NORMAL"
_last_toggle_ms = 0
_led_phase = False

# Wi-Fi treptanje
_WIFI_BLINK_PERIOD_MS = 1000
_wifi_blink_enabled = False
_wifi_last_toggle_ms = 0
_wifi_on = False

# OTA treptanje
_OTA_BLINK_PERIOD_MS = 200
_OTA_ERROR_PERIOD_MS = 700

# NEW: dok ne stigne prvo AQI setovanje, držimo startup belu kao stable
_has_first_aqi = False

def _show(color):
    global _current
    _current = color
    try:
        np[0] = color
        np.write()
    except Exception as ex:
        print("LED error:", ex)

def set_mode(mode: str):
    global current_mode
    current_mode = mode
    print("LED MODE =>", current_mode)

def set_startup_white():
    """Pozovi na boot: stalno belo dok ne dođe prvo AQI."""
    global _stable_color, _has_first_aqi
    _has_first_aqi = False
    _stable_color = COLOR_STARTUP_WHITE
    _show(COLOR_STARTUP_WHITE)

def set_boot():
    global _stable_color
    _stable_color = COLOR_BOOT
    _show(COLOR_BOOT)

def set_ok():
    global _stable_color
    # ako još nemamo AQI, OK ne treba da pregazi startup belu
    if not _has_first_aqi:
        return
    _stable_color = COLOR_OK
    _show(COLOR_OK)

def set_measuring():
    """
    Ranije: prolazno belo.
    _show(COLOR_MEASURING)
    Sada: NO-OP (da na SEN55 ne drži belo 30+ sekundi dok meri).
    """
    return

def set_uploading():
    """
    Ostaje radi kompatibilnosti, ali ga ne koristimo više.
    _show(COLOR_UPLOADING)
    """
    # no-op da SC ne menja boju
    return

def is_off():
    # Dok je Wi-Fi treptanje aktivno, ne tretiramo LED kao off
    if _wifi_blink_enabled:
        return False
    return _current == (0, 0, 0)

def set_aqi_level(level: int):
    """
    level 0..5 – bilo šta van toga ograničimo.
    Ovo postaje 'stabilna' boja, i ujedno gasi startup belu.
    """
    global _stable_color, _has_first_aqi
    if level is None:
        return

    if level < 0:
        level = 0
    if level >= len(AQI_COLORS):
        level = len(AQI_COLORS) - 1

    color = AQI_COLORS[level]
    _stable_color = color
    _has_first_aqi = True
    _show(color)

def set_wifi_fail_mode(enabled: bool = True):
    """
    Uključi/isključi blink mod za Wi-Fi fail.
    """
    global _wifi_blink_enabled, _wifi_last_toggle_ms, _wifi_on

    if enabled and not _wifi_blink_enabled:
        _wifi_blink_enabled = True
        _wifi_last_toggle_ms = time.ticks_ms()
        _wifi_on = True
        _show(COLOR_WIFI_FAIL)
    elif not enabled and _wifi_blink_enabled:
        _wifi_blink_enabled = False
        _wifi_on = False
        restore()

def tick():
    """
    Poziva se u glavnoj petlji.
    Prioritet: OTA modovi > WiFi blink.
    """
    global _wifi_last_toggle_ms, _wifi_on, _last_toggle_ms, _led_phase

    now = time.ticks_ms()

    # OTA modovi imaju prioritet
    if current_mode == "OTA_IN_PROGRESS":
        if time.ticks_diff(now, _last_toggle_ms) >= _OTA_BLINK_PERIOD_MS:
            _last_toggle_ms = now
            _led_phase = not _led_phase
            _show((X_BRIGHT, 0, 0) if _led_phase else (0, 0, X_BRIGHT))
        return

    if current_mode == "OTA_ERROR":
        if time.ticks_diff(now, _last_toggle_ms) >= _OTA_ERROR_PERIOD_MS:
            _last_toggle_ms = now
            _led_phase = not _led_phase
            _show((X_BRIGHT, 0, 0) if _led_phase else (0, 0, 0))
        return

    # Wi-Fi blink
    if not _wifi_blink_enabled:
        return

    if time.ticks_diff(now, _wifi_last_toggle_ms) >= _WIFI_BLINK_PERIOD_MS:
        _wifi_last_toggle_ms = now
        _wifi_on = not _wifi_on
        _show(COLOR_WIFI_FAIL if _wifi_on else (0, 0, 0))

def set_off():
    _show((0, 0, 0))

def restore():
    """Vrati na poslednje 'stabilno' stanje (startup belo ili AQI)."""
    global _stable_color
    if _stable_color is not None:
        _show(_stable_color)
    elif _current is not None:
        np[0] = _current
        np.write()
