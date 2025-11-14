# sensor_bme280.py
# BME280 / BMP280 senzor u stilu projekta
from machine import I2C, Pin
import time
from sensor_base import BaseEnvSensor

# isti I2C kao i za sen55 / sps30
_i2c = I2C(0, scl=Pin(5), sda=Pin(4), freq=100000)

# default adresa – većina modula je 0x76, neki su 0x77
_DEFAULT_ADDR = 0x76


class _RawBME280:
    """
    Minimalni interni driver za BME280/BMP280 da ne uvodimo dodatne fajlove.
    Dovoljno za: temperatura (°C), pritisak (hPa), vlažnost (%).
    Ako je BMP280 – vlažnost će ostati None.
    """
    def __init__(self, i2c, address=_DEFAULT_ADDR):
        self.i2c = i2c
        self.address = address

        # provera ko je
        chip_id = self.i2c.readfrom_mem(self.address, 0xD0, 1)[0]
        # 0x60 = BME280, 0x58 = BMP280
        self.is_bme = (chip_id == 0x60)

        # reset
        try:
            self.i2c.writeto_mem(self.address, 0xE0, b'\xB6')
        except Exception:
            pass
        time.sleep_ms(10)

        # učitaj kalibracione podatke
        self._load_calibration()

        # ctrl_hum (samo za BME)
        if self.is_bme:
            # oversampling x1
            self.i2c.writeto_mem(self.address, 0xF2, b'\x01')

        # ctrl_meas: temp oversampling x1, pressure oversampling x1, normal mode
        self.i2c.writeto_mem(self.address, 0xF4, b'\x27')
        # config: standby 0.5ms, filter off
        self.i2c.writeto_mem(self.address, 0xF5, b'\xA0')

    def _load_calibration(self):
        # kalibracija za temp i pritisak
        dig = self.i2c.readfrom_mem(self.address, 0x88, 24)
        self.dig_T1 = dig[0] | (dig[1] << 8)
        self.dig_T2 = self._to_signed(dig[2] | (dig[3] << 8), 16)
        self.dig_T3 = self._to_signed(dig[4] | (dig[5] << 8), 16)
        self.dig_P1 = dig[6] | (dig[7] << 8)
        self.dig_P2 = self._to_signed(dig[8] | (dig[9] << 8), 16)
        self.dig_P3 = self._to_signed(dig[10] | (dig[11] << 8), 16)
        self.dig_P4 = self._to_signed(dig[12] | (dig[13] << 8), 16)
        self.dig_P5 = self._to_signed(dig[14] | (dig[15] << 8), 16)
        self.dig_P6 = self._to_signed(dig[16] | (dig[17] << 8), 16)
        self.dig_P7 = self._to_signed(dig[18] | (dig[19] << 8), 16)
        self.dig_P8 = self._to_signed(dig[20] | (dig[21] << 8), 16)
        self.dig_P9 = self._to_signed(dig[22] | (dig[23] << 8), 16)

        # vlažnost samo ako je BME
        if self.is_bme:
            self.dig_H1 = self.i2c.readfrom_mem(self.address, 0xA1, 1)[0]
            digH = self.i2c.readfrom_mem(self.address, 0xE1, 7)
            self.dig_H2 = self._to_signed(digH[0] | (digH[1] << 8), 16)
            self.dig_H3 = digH[2]
            e4 = digH[3]
            e5 = digH[4]
            e6 = digH[5]
            self.dig_H4 = (e4 << 4) | (e5 & 0x0F)
            self.dig_H5 = (e6 << 4) | (e5 >> 4)
            self.dig_H6 = self._to_signed(digH[6], 8)
        else:
            self.dig_H1 = None

    @staticmethod
    def _to_signed(val, bits):
        if val & (1 << (bits - 1)):
            val -= 1 << bits
        return val

    def read(self):
        # raw podaci: pressure(3) + temp(3) + humidity(2) (ako BME)
        data = self.i2c.readfrom_mem(self.address, 0xF7, 8)
        # pressure
        adc_p = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
        # temperature
        adc_t = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)

        # temperatura
        var1 = (((adc_t >> 3) - (self.dig_T1 << 1)) * self.dig_T2) >> 11
        var2 = (((((adc_t >> 4) - self.dig_T1) * ((adc_t >> 4) - self.dig_T1)) >> 12) * self.dig_T3) >> 14
        t_fine = var1 + var2
        temp = (t_fine * 5 + 128) >> 8   # u stotinkama °C
        temperature = temp / 100.0

        # pritisak
        var1 = t_fine - 128000
        var2 = var1 * var1 * self.dig_P6
        var2 = var2 + ((var1 * self.dig_P5) << 17)
        var2 = var2 + (self.dig_P4 << 35)
        var1 = ((var1 * var1 * self.dig_P3) >> 8) + ((var1 * self.dig_P2) << 12)
        var1 = (((1 << 47) + var1) * self.dig_P1) >> 33
        if var1 == 0:
            pressure = None
        else:
            p = 1048576 - adc_p
            p = (((p << 31) - var2) * 3125) // var1
            var1 = (self.dig_P9 * (p >> 13) * (p >> 13)) >> 25
            var2 = (self.dig_P8 * p) >> 19
            p = ((p + var1 + var2) >> 8) + (self.dig_P7 << 4)
            pressure = p / 25600.0  # hPa

        # vlažnost
        humidity = None
        if self.is_bme:
            # ponovo očitaj da bi uzeo i humidity registar
            data2 = self.i2c.readfrom_mem(self.address, 0xF7, 8)  # već imamo, ali da ne komplikujemo
            # ustvari humidity je u 0xFD/0xFE kad se čita 8 bajtova
            hum_raw = (data[6] << 8) | data[7]
            v_x1_u32r = t_fine - 76800
            v_x1_u32r = (((((hum_raw << 14) - (self.dig_H4 << 20) - (self.dig_H5 * v_x1_u32r)) + 16384) >> 15)
                         * (((((((v_x1_u32r * self.dig_H6) >> 10) * (((v_x1_u32r * self.dig_H3) >> 11) + 32768)) >> 10)
                              + 2097152) * self.dig_H2 + 8192) >> 14))
            v_x1_u32r = v_x1_u32r - (((((v_x1_u32r >> 15) * (v_x1_u32r >> 15)) >> 7) * self.dig_H1) >> 4)
            v_x1_u32r = 0 if v_x1_u32r < 0 else v_x1_u32r
            v_x1_u32r = 419430400 if v_x1_u32r > 419430400 else v_x1_u32r
            humidity = (v_x1_u32r >> 12) / 1024.0

        return temperature, humidity, pressure


class Bme280Sensor(BaseEnvSensor):
    def __init__(self, address=_DEFAULT_ADDR):
        self._bme = _RawBME280(_i2c, address)

    def get_supported_fields(self):
        return ["temperature", "humidity", "pressure"]

    def measure_one_minute(self,
                           samples_count: int = 5,
                           interval_seconds: int = 1,
                           trim_extremes: bool = True):
        samples = []

        for _ in range(samples_count):
            try:
                t, h, p = self._bme.read()
                samples.append((t, h, p))
            except Exception:
                # ako jedan sample pukne, samo preskoči
                pass
            time.sleep(interval_seconds)

        if not samples:
            return None

        def avg(idx):
            vals = [s[idx] for s in samples if s[idx] is not None]
            if not vals:
                return None
            vals = sorted(vals)
            if trim_extremes and len(vals) > 2:
                vals = vals[1:-1]
            return sum(vals) / len(vals)

        return {
            "temperature": avg(0),
            "humidity": avg(1),   # može biti None ako je BMP
            "pressure": avg(2),
        }
