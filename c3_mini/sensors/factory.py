# sensors/factory.py
from machine import Pin, I2C
from boards.selector import load_board_config

from sensors.sen55 import Sen55Sensor
from sensors.sps30 import Sps30Sensor
from sensors.bme280 import Bme280Sensor
from sensors.sds011 import SDS011Sensor


def _as_list(val):
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def create_sensors(device_cfg: dict) -> list:
    """
    device_cfg – config['device'] iz config.json, npr:
    {
      "uid": "...",
      "board": "c3_lolin",
      "sensors": ["sen55", "bme280"]
    }

    Pravilo:
    - ako device_cfg['sensors'] postoji -> instanciraj samo te senzore (scan samo za log)
    - ako ne postoji -> autodetect po i2c.scan()
    """
    cfg = load_board_config(device_cfg)

    i2c = I2C(
        cfg.I2C_ID,
        sda=Pin(cfg.I2C_SDA),
        scl=Pin(cfg.I2C_SCL),
        freq=getattr(cfg, "I2C_FREQ", 100_000),
    )

    # scan samo kao dijagnostika (i kao input za autodetect)
    found = set()
    try:
        found = set(i2c.scan())
        print("[I2C] found:", [hex(x) for x in sorted(found)])
    except Exception as ex:
        print("[I2C] scan failed:", ex)

    enabled = _as_list(device_cfg.get("sensors"))
    enabled = [s.strip().lower() for s in enabled if isinstance(s, str)]

    sensors = []

    # --- eksplicitno (po configu) ---
    if enabled:
        for name in enabled:
            try:
                if name == "sen55":
                    sensors.append(Sen55Sensor(i2c))
                elif name == "sps30":
                    sensors.append(Sps30Sensor(i2c))
                elif name == "bme280":
                    # opcioni override adrese:
                    # device_cfg["bme280_address"] = 0x76 ili 0x77
                    addr = device_cfg.get("bme280_address")
                    sensors.append(Bme280Sensor(i2c, address=addr) if addr else Bme280Sensor(i2c))
                elif name == "sds011":
                    sensors.append(SDS011Sensor(
                        uart_id=getattr(cfg, "SDS011_UART_ID", 1),
                        tx_pin=getattr(cfg, "SDS011_UART_TX", 21),
                        rx_pin=getattr(cfg, "SDS011_UART_RX", 20),
                        baudrate=getattr(cfg, "SDS011_UART_BAUD", 9600),
                        warmup_ms=device_cfg.get("sds011_warmup_ms", 3000),
                        keep_awake_pm_threshold=device_cfg.get("sds011_keep_awake_pm_threshold", 200.0),
                        keep_awake_hold_s=device_cfg.get("sds011_keep_awake_hold_s", 5 * 60),
                        suspect_pm_threshold=device_cfg.get("sds011_suspect_pm_threshold", 800.0),
                        suspect_hold_s=device_cfg.get("sds011_suspect_hold_s", 10 * 60),
                        debug=bool(device_cfg.get("sds011_debug", False)),
                    ))
                else:
                    print("Unknown sensor in config:", name)
            except Exception as ex:
                print("Failed to init sensor", name, "->", ex)

        return sensors

    # --- autodetect (best effort) ---
    # Napomena: SEN55 i SPS30 imaju 0x69 u fajlovima,
    # scan ne može da ih razlikuje -> pokušamo oba, ko ne radi otpada.
    try:
        if 0x69 in found:
            try:
                sensors.append(Sen55Sensor(i2c))
            except Exception as ex:
                print("Failed to init sensor sen55 ->", ex)
            try:
                sensors.append(Sps30Sensor(i2c))
            except Exception as ex:
                print("Failed to init sensor sps30 ->", ex)

        if 0x76 in found or 0x77 in found:
            try:
                sensors.append(Bme280Sensor(i2c))
            except Exception as ex:
                print("Failed to init sensor bme280 ->", ex)
    except Exception as ex:
        print("Autodetect failed ->", ex)

    return sensors
