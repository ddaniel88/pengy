# sensor_sen55.py
from machine import Pin, I2C
import time
from sensor_base import BaseEnvSensor

I2C_ADDR = 0x69

# prilagodi pinove po svojoj pločici
i2c = I2C(0, scl=Pin(5), sda=Pin(4), freq=100000)


def _crc8(data):
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x31
            else:
                crc <<= 1
            crc &= 0xFF
    return crc


def _send_cmd(cmd):
    i2c.writeto(I2C_ADDR, bytes([(cmd >> 8) & 0xFF, cmd & 0xFF]))


def _start_measurement():
    _send_cmd(0x0021)


def _stop_measurement():
    _send_cmd(0x0104)


def _data_ready():
    _send_cmd(0x0202)
    data = i2c.readfrom(I2C_ADDR, 3)
    if _crc8(data[0:2]) != data[2]:
        return False
    return (data[1] & 0x01) == 1


def _read_raw():
    _send_cmd(0x03C4)
    time.sleep_ms(20)
    return i2c.readfrom(I2C_ADDR, 24)


def _parse_values(raw):
    # layout koji smo već koristili: 8 vrednosti po 3 bajta (2 + crc)
    def val(offset, scale):
        word = raw[offset:offset+2]
        crc = raw[offset+2]
        if _crc8(word) != crc:
            return None
        num = (word[0] << 8) | word[1]
        if num == 0xFFFF:
            return None
        return num / scale

    return {
        "pm1_0":       val(0, 10),
        "pm2_5":       val(3, 10),
        "pm4_0":       val(6, 10),
        "pm10":        val(9, 10),
        "humidity":    val(12, 100),
        "temperature": val(15, 200),
        "voc_index":   val(18, 10),
        "nox_index":   val(21, 10),
    }


class Sen55Sensor(BaseEnvSensor):
    """
    Implementacija za Sensirion SEN55 na ESP32-C3.
    """
    def __init__(self, ready_retries: int = 3):
        self.ready_retries = ready_retries

    def get_supported_fields(self):
        # OVO je bitno i za /status i za future uploadere
        return [
            "pm1_0",
            "pm2_5",
            "pm4_0",
            "pm10",
            "temperature",
            "humidity",
            "voc_index",
            "nox_index",
        ]

    def measure_one_minute(self,
                           samples_count: int = 5,
                           interval_seconds: int = 1,
                           trim_extremes: bool = True):
        """
        Pokrene merenje, skupi više uzoraka, baci min/max ako treba,
        vrati jedan “minutni” dict sa pm2_5, temperature, humidity.
        Ako ništa nije uspeo da izmeri – vrati None.
        """
        _start_measurement()
        time.sleep(1)  # warmup

        samples = []

        for _ in range(samples_count):
            ok = False
            for _ in range(self.ready_retries):
                if _data_ready():
                    raw = _read_raw()
                    parsed = _parse_values(raw)
                    samples.append(parsed)
                    ok = True
                    break
                time.sleep_ms(300)

            # čak i ako nije ok, idemo dalje na sledeći sample
            time.sleep(interval_seconds)

        _stop_measurement()

        if not samples:
            return None

        def trimmed_average(key):
            values = [s[key] for s in samples if s and s.get(key) is not None]
            if not values:
                return None
            values = sorted(values)
            if trim_extremes and len(values) > 2:
                values = values[1:-1]
            return sum(values) / len(values)

        return {
            "pm1_0": trimmed_average("pm1_0"),
            "pm2_5": trimmed_average("pm2_5"),
            "pm4_0": trimmed_average("pm4_0"),
            "pm10": trimmed_average("pm10"),
            "temperature": trimmed_average("temperature"),
            "humidity": trimmed_average("humidity"),
            "voc_index": trimmed_average("voc_index"),
            "nox_index": trimmed_average("nox_index"),
        }
