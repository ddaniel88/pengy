# led_status.py
import machine, neopixel
import time

PIN = 7
NUM = 1
BRIGHT = 45
X_BRIGHT = 150

np = neopixel.NeoPixel(machine.Pin(PIN), NUM)

# osnovne boje (r, g, b)
COLOR_BOOT = (180, 120, 0)          # žućkasto kad se diže
COLOR_OK = (0, BRIGHT, 0)                 # zeleno kad je online
COLOR_WIFI_FAIL = (220, 0, 0)        # crveno kad nije online

# merenje = belo (kratko)
COLOR_MEASURING = (80, 80, 80)
COLOR_UPLOADING = (0, 60, 180)

# AQI boje (sens.comm stil / 6 nivoa)
# 0 good, 1 fair, 2 moderate, 3 poor, 4 very poor, 5 extremely poor
AQI_COLORS = [
    (0, 200, 120),   # good - tirkizno
    (0, 160, 40),   # fair - zelenkasto
    (200, 180, 0),   # moderate - žućkasto
    (X_BRIGHT, 70, 0), # 3 - narandžasto / lošije
    (X_BRIGHT, 0, 0),             # 4 - crveno / loše
    (X_BRIGHT, 0, 90),      # 5 - ljubičasto / vrlo loše
]

_current = (0, 0, 0)     # šta trenutno svetli
_stable_color = None     # šta da vratimo u restore (obično AQI)

current_mode = "NORMAL"   # global
_last_toggle_ms = 0
_led_phase = False  # za naizmenične boje / on-off

# Wi-Fi treptanje
_WIFI_BLINK_PERIOD_MS = 1000
_wifi_blink_enabled = False
_wifi_last_toggle_ms = 0
_wifi_on = False  # da li je trenutno upaljena crvena u blink modu

# OTA treptanje
_OTA_BLINK_PERIOD_MS = 200     # crveno / plavo brzo
_OTA_ERROR_PERIOD_MS = 700     # crveno sporo on/off

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

def set_boot():
    global _stable_color
    _stable_color = COLOR_BOOT
    _show(COLOR_BOOT)


def set_ok():
    global _stable_color
    _stable_color = COLOR_OK
    _show(COLOR_OK)


def set_measuring():
    """Prolazno belo, ne menja _stable_color."""
    _show(COLOR_MEASURING)


def set_uploading():
    """Prolazna plava, ne menja _stable_color."""
    _show(COLOR_UPLOADING)


def is_off():
    # Dok je Wi-Fi treptanje aktivno, ne tretiramo LED kao "off",
    # da main ne bi radio restore() na žutu / AQI boju.
    try:
        if _wifi_blink_enabled:
            return False
    except NameError:
        # ako iz nekog razloga promenljiva ne postoji, fallback
        pass

    return _current == (0, 0, 0)


def set_aqi_level(level: int):
    """
    level 0..5 – bilo šta van toga ćemo da ograničimo
    ovo je 'stabilna' boja koju vraćamo dok je sve ok
    """
    global _stable_color
    if level is None:
        return
    if level < 0:
        level = 0
    if level >= len(AQI_COLORS):
        level = len(AQI_COLORS) - 1

    color = AQI_COLORS[level]
    _stable_color = color
    _show(color)


def set_wifi_fail_mode(enabled: bool = True):
    """
    Uključi/isključi blink mod za Wi-Fi fail.
    Ne radi sam treptanje – to radi tick(), samo postavlja stanje.
    """
    global _wifi_blink_enabled, _wifi_last_toggle_ms, _wifi_on

    if enabled and not _wifi_blink_enabled:
        _wifi_blink_enabled = True
        _wifi_last_toggle_ms = time.ticks_ms()
        _wifi_on = True
        _show(COLOR_WIFI_FAIL)  # start sa upaljenom crvenom
    elif not enabled and _wifi_blink_enabled:
        _wifi_blink_enabled = False
        _wifi_on = False
        # vrati se na “normalnu” stabilnu boju
        restore()


def tick():
    """
    Poziva se u glavnoj petlji.
    - Ako je OTA mod aktivan, radi OTA treperenje.
    - Inače, ako je Wi-Fi blink aktivan, treperi crveno..
    """
    global _wifi_last_toggle_ms, _wifi_on, _last_toggle_ms, _led_phase

    now = time.ticks_ms()

    # --- OTA modovi imaju prioritet nad svime ---

    if current_mode == "OTA_IN_PROGRESS":
        # policijsko crveno / plavo
        if time.ticks_diff(now, _last_toggle_ms) >= _OTA_BLINK_PERIOD_MS:
            _last_toggle_ms = now
            _led_phase = not _led_phase
            if _led_phase:
                _show((X_BRIGHT, 0, 0))   # crveno
            else:
                _show((0, 0, X_BRIGHT))   # plavo
        return

    if current_mode == "OTA_ERROR":
        # sporo crveno on/off
        if time.ticks_diff(now, _last_toggle_ms) >= _OTA_ERROR_PERIOD_MS:
            _last_toggle_ms = now
            _led_phase = not _led_phase
            if _led_phase:
                _show((X_BRIGHT, 0, 0))   # crveno
            else:
                _show((0, 0, 0))          # ugašeno
        return

    # --- Wi-Fi blink ---

    if not _wifi_blink_enabled:
        return
    if time.ticks_diff(now, _wifi_last_toggle_ms) >= _WIFI_BLINK_PERIOD_MS:
        _wifi_last_toggle_ms = now
        _wifi_on = not _wifi_on
        if _wifi_on:
            _show(COLOR_WIFI_FAIL)
        else:
            _show((0, 0, 0))


def set_off():
    _show((0, 0, 0))


def restore():
    """Vrati na poslednje 'stabilno' stanje (npr. posle merenja)."""
    global _stable_color
    if _stable_color is not None:
        _show(_stable_color)
    elif _current is not None:
        np[0] = _current
        np.write()