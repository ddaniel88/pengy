# sensors/sen55.py
import time
from sensors.base import BaseEnvSensor

I2C_ADDR = 0x69

# ----------------------------------------------------------------------
# Low-level helpers
# ----------------------------------------------------------------------
def _crc8(data: bytes) -> int:
    """CRC-8 sa polinomom 0x31, init 0xFF – prema Sensirion datasheet-u."""
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


def _send_cmd(i2c, cmd: int) -> None:
    """Pošalji 16-bit komandu senzoru."""
    i2c.writeto(I2C_ADDR, bytes([(cmd >> 8) & 0xFF, cmd & 0xFF]))
    
    
def _read_status(i2c) -> int:
    data = i2c.readfrom(I2C_ADDR, 3)
    return (data[0] << 8) | data[1]


def _read_values(i2c) -> bytes:
    return i2c.readfrom(I2C_ADDR, 24)


def _start_measurement(i2c) -> None:
    """Start Measurement (0x0021) – full mode (PM + RHT + VOC + NOx)."""
    _send_cmd(i2c, 0x0021)


def _stop_measurement(i2c) -> None:
    """Stop Measurement (0x0104)."""
    _send_cmd(i2c, 0x0104)


def _data_ready(i2c) -> bool:
    """Read Data-Ready Flag (0x0202)."""
    _send_cmd(i2c, 0x0202)
    data = i2c.readfrom(I2C_ADDR, 3)
    if _crc8(data[0:2]) != data[2]:
        return False
    # Byte[1] bit0 = 1 -> new measurements ready
    return (data[1] & 0x01) == 1


def _read_raw(i2c) -> bytes:
    """
    Read Measured Values (0x03C4).
    Datasheet kaže: posle komande sačeka ~20ms pa čitaj 24 bajta.
    """
    _send_cmd(i2c, 0x03C4)
    time.sleep_ms(20)
    return i2c.readfrom(I2C_ADDR, 24)


def _parse_values(raw: bytes) -> dict | None:
    """
    Parsira 24 bajta po tabeli iz datasheet-a:

      PM1.0, PM2.5, PM4.0, PM10   -> uint16 / 10
      RH [%]                      -> int16 / 100
      Temp [°C]                   -> int16 / 200
      VOC Index, NOx Index        -> int16 / 10

    Ako je vrednost 0xFFFF ili CRC ne valja -> vrati None za taj field.
    """
    if len(raw) != 24:
        return None

    def val(offset: int, scale: int):
        word = raw[offset : offset + 2]
        crc = raw[offset + 2]
        if _crc8(word) != crc:
            return None
        num = (word[0] << 8) | word[1]
        if num == 0xFFFF:
            return None
        # signed / unsigned je ovde praktično nebitno za normalne opsege,
        # ali po datasheet-u RH / T / VOC / NOx su int16.
        if offset >= 12:  # od RH naviše su int16
            if num & 0x8000:
                num = num - 0x10000
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


# ----------------------------------------------------------------------
# High-level senzor za ostatak sistema
# ----------------------------------------------------------------------
class Sen55Sensor(BaseEnvSensor):
    """
    Implementacija za Sensirion SEN55 na ESP32-C3.

    - measure_one_minute() radi više uzoraka, skida ekstreme (opciono) i
      vraća prosek.
    - ima opcioni temperaturni ofset (lokalno, ne pišemo u senzor).
    - ima fallback na poslednje dobro merenje ako trenutni ciklus padne.
    """

    def __init__(
        self,
        i2c,
        ready_retries: int = 3,
        temp_offset_c: float = 0.0,
        fallback_to_last: bool = True,
    ):
        """
        :param ready_retries: koliko puta da pokušamo da čekamo data-ready
                              za svaki sample (tipično 2–3 puta).
        :param temp_offset_c: lokalni temperaturni ofset u °C
                              (npr. -1.5 ako senzor pokazuje +1.5°C).
        :param fallback_to_last: ako nema NIJEDNOG uzorka u ovom ciklusu,
                                 vrati poslednje dobro merenje umesto None.
        """
        self.i2c = i2c
        self.ready_retries = ready_retries
        self.temp_offset_c = temp_offset_c
        self.fallback_to_last = fallback_to_last
        self._last_good: dict | None = None

    # ------------------------------------------------------------------
    # Interfejs koji koristi SensorManager
    # ------------------------------------------------------------------
    def get_supported_fields(self):
        # Ovo je bitno i za /status i za uploade (Sensor.Community itd.)
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

    def measure_one_minute(
        self,
        samples_count: int = 10,
        interval_seconds: int = 2,
        trim_extremes: bool = True,
    ):
        """
        Pokrene merenje, skupi više uzoraka, baci min/max ako treba,
        vrati jedan “minutni” dict sa prosečnim vrednostima.

        Ako ništa nije uspeo da izmeri:
        - ako imamo fallback_to_last=True i postoji _last_good -> vrati to
        - inače vrati None
        """
        try:
            _start_measurement(self.i2c)
            # kratki warmup da se stabilizuje
            time.sleep(5)
        except OSError:
            # I2C problem već na startu – ne rušimo sistem
            return self._last_good if self.fallback_to_last else None

        samples = []

        try:
            for _ in range(samples_count):
                parsed = None

                # više pokušaja da sačekamo da data-ready bude 1
                for _ in range(self.ready_retries):
                    try:
                        if _data_ready(self.i2c):
                            raw = _read_raw(self.i2c)
                            parsed = _parse_values(raw)
                            break
                    except OSError:
                        # verovatno reset / glitch na I2C-u
                        # pokušaj samo da ponovo startuješ merenje
                        try:
                            _start_measurement(self.i2c)
                        except Exception:
                            pass
                    # nije bilo spremno ili je bilo sitnog problema,
                    # pričekaj malo pa probaj opet
                    time.sleep_ms(300)

                # ako ipak nismo dobili validan sample, samo nastavi dalje
                if parsed:
                    # primeni lokalni temperaturni ofset ako je podešen
                    if self.temp_offset_c and parsed.get("temperature") is not None:
                        parsed["temperature"] = (
                            parsed["temperature"] + self.temp_offset_c
                        )
                    samples.append(parsed)

                # razmak između uzoraka
                time.sleep(interval_seconds)

        finally:
            # uvek pokušaj da zaustaviš merenje, čak i ako je nešto puklo
            try:
                _stop_measurement(self.i2c)
            except Exception:
                pass

        if not samples:
            # nema nijednog validnog uzorka u ovom ciklusu
            if self.fallback_to_last and self._last_good is not None:
                return self._last_good
            return None

        # helper za trimmed average
        def trimmed_average(key: str):
            values = [s[key] for s in samples if s and s.get(key) is not None]
            if not values:
                return None
            values = sorted(values)
            if trim_extremes and len(values) > 2:
                # odbaci jedan min i jedan max
                values = values[1:-1]
            return sum(values) / len(values)

        result = {
            "pm1_0":       trimmed_average("pm1_0"),
            "pm2_5":       trimmed_average("pm2_5"),
            "pm4_0":       trimmed_average("pm4_0"),
            "pm10":        trimmed_average("pm10"),
            "temperature": trimmed_average("temperature"),
            "humidity":    trimmed_average("humidity"),
            "voc_index":   trimmed_average("voc_index"),
            "nox_index":   trimmed_average("nox_index"),
        }

        # zapamti poslednje dobro merenje (i ako neki ključ ima None)
        self._last_good = result
        return result

