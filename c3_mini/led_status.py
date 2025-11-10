# led_status.py
import machine, neopixel

PIN = 7
NUM = 1
BRIGHT = 20
X_BRIGHT = 200

np = neopixel.NeoPixel(machine.Pin(PIN), NUM)

# osnovne boje (r, g, b)
COLOR_BOOT = (BRIGHT, BRIGHT, 0)          # žućkasto kad se diže
COLOR_OK = (0, BRIGHT, 0)                 # zeleno kad je online
COLOR_WIFI_FAIL = (X_BRIGHT, 0, 0)        # crveno kad nije online

# merenje = belo (kratko)
COLOR_MEASURING = (BRIGHT, BRIGHT, BRIGHT)

# AQI boje (sens.comm stil / 6 nivoa)
# 0 good, 1 fair, 2 moderate, 3 poor, 4 very poor, 5 extremely poor
COLOR_AQI = [
    (0, 180, 180),   # good - tirkizno
    (0, 150, 10),   # fair - zelenkasto
    (180, 160, 0),   # moderate - žućkasto
    (200, 0, 0),     # poor - crveno
    (150, 0, 50),    # very poor - bordo
    (120, 0, 120),   # extremely poor - ljubičasto
]

_current = None          # šta trenutno svetli
_stable_color = None     # šta da vratimo u restore (obično AQI)

def _show(color):
    global _current
    _current = color
    np[0] = color
    np.write()

def set_boot():
    global _stable_color
    _stable_color = COLOR_BOOT
    _show(COLOR_BOOT)

def set_ok():
    global _stable_color
    _stable_color = COLOR_OK
    _show(COLOR_OK)

def set_wifi_fail():
    # ovo je status koji ima veći prioritet od AQI
    _show(COLOR_WIFI_FAIL)

def set_measuring():
    # kratko belo – posle ovoga u kodu pozoveš restore() ili set_aqi_level
    _show(COLOR_MEASURING)

def set_aqi_level(level: int):
    """
    level 0..5 – bilo šta van toga ćemo da ograničimo
    ovo je 'stabilna' boja koju vraćamo dok je sve ok
    """
    global _stable_color
    if level is None:
        # ako nemamo podatak, ne diramo boju
        return
    if level < 0:
        level = 0
    if level >= len(COLOR_AQI):
        level = len(COLOR_AQI) - 1
    color = COLOR_AQI[level]
    _stable_color = color
    _show(color)

def set_off():
    _show((0, 0, 0))

def restore():
    """Vrati na poslednje 'stabilno' stanje (npr. posle merenja)."""
    global _stable_color
    if _stable_color is not None:
        _show(_stable_color)
    elif _current is not None:
        # fallback
        np[0] = _current
        np.write()
