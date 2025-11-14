# sensor_sps30.py
# SPS30
import time
import struct
from machine import Pin, I2C
from sensor_base import BaseEnvSensor

I2C_ADDR = 0x69

i2c = I2C(0, scl=Pin(5), sda=Pin(4), freq=100000)


def _crc8(two_bytes: bytes) -> int:
    crc = 0xFF
    for b in two_bytes:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


class Sps30Sensor(BaseEnvSensor):
    """
    SPS30 adapter.
    Vraća samo mass-koncentracije (prve 4 vrednosti):
      - pm1
      - pm2_5
      - pm4
      - pm10
    Ostale (number concentration...) za sada ignorišemo.
    """

    def __init__(self):
        self._started = False

    def get_supported_fields(self):
        # ovde koristimo nazive kao u config.json
        return ["pm1", "pm2_5", "pm4", "pm10"]

    def _start_measurement(self):
        # komanda 0x0010, payload 0x03 0x00 (float), pa CRC
        payload = b"\x03\x00"
        c = _crc8(payload)
        packet = b"\x00\x10" + payload + bytes([c])
        i2c.writeto(I2C_ADDR, packet)
        self._started = True

    def _read_raw_frame(self):
        # set pointer na 0x0300 pa čitanje 60 bajtova
        i2c.writeto(I2C_ADDR, b"\x03\x00")
        data = i2c.readfrom(I2C_ADDR, 60)
        return data

    def _parse_10_floats(self, data: bytes):
        vals = []
        for offset in range(0, 60, 6):
            hi = data[offset:offset+2]
            c1 = data[offset+2]
            lo = data[offset+3:offset+5]
            c2 = data[offset+5]

            if _crc8(hi) != c1 or _crc8(lo) != c2:
                vals.append(None)
                continue

            raw = hi + lo
            vals.append(struct.unpack(">f", raw)[0])
        return vals

    def measure_one_minute(self,
                           samples_count: int = 5,
                           interval_seconds: int = 1,
                           trim_extremes: bool = True):
        # pokreni merenje samo prvi put
        if not self._started:
            self._start_measurement()
            # SPS30 merenje ide u pozadini, ali dajemo mu malo vazduha
            time.sleep(1)

        samples = []

        for _ in range(samples_count):
            try:
                raw = self._read_raw_frame()
                vals = self._parse_10_floats(raw)
                if vals and vals[0] is not None:
                    # prve 4 su nam bitne
                    sample = {
                        "pm1": vals[0],
                        "pm2_5": vals[1],
                        "pm4": vals[2],
                        "pm10": vals[3],
                    }
                    samples.append(sample)
            except Exception:
                # ako jedan sample omane, samo nastavi
                pass

            time.sleep(interval_seconds)

        if not samples:
            return None

        # helper za proseke
        def avg(key):
            values = [s[key] for s in samples if s.get(key) is not None]
            if not values:
                return None
            values = sorted(values)
            if trim_extremes and len(values) > 2:
                values = values[1:-1]
            return sum(values) / len(values)

        return {
            "pm1": avg("pm1"),
            "pm2_5": avg("pm2_5"),
            "pm4": avg("pm4"),
            "pm10": avg("pm10"),
        }
