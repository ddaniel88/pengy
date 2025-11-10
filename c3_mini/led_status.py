# led_status.py
import machine, neopixel

PIN = 7              # na tvojoj C3 mini
NUM = 1
BRIGHT = 2          # 0-255, malo da ne bode oči
X_BRIGHT = 200

np = neopixel.NeoPixel(machine.Pin(PIN), NUM)

# boje (r, g, b)
COLOR_BOOT = (BRIGHT, BRIGHT, 0)         # žućkasto kad se diže
COLOR_OK = (0, BRIGHT, 0)           # zeleno kad je online
COLOR_WIFI_FAIL = (X_BRIGHT, 0, 0)         # crveno kad nije online
COLOR_MEASURING = (BRIGHT, 0, BRIGHT)    # ljubičasto
COLOR_UPLOADING = (BRIGHT, BRIGHT, BRIGHT)  # belo

_current = None

def _show(color):
    global _current
    _current = color
    np[0] = color
    np.write()

def set_boot():
    _show(COLOR_BOOT)

def set_ok():
    _show(COLOR_OK)

def set_wifi_fail():
    _show(COLOR_WIFI_FAIL)

def set_measuring():
    _show(COLOR_MEASURING)

def set_uploading():
    _show(COLOR_UPLOADING)

def set_off():
    _show((0, 0, 0))

def restore():
    """Vrati na poslednje 'stabilno' stanje (npr. posle merenja)."""
    if _current is not None:
        np[0] = _current
        np.write()
