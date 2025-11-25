# uploader/sensor_community.py
import time
import ujson
import led_status
import gc
from uploader.base import BaseUploader

# pokušaćemo da koristimo urequests (najčešće postoji na ESP32)
try:
    import urequests as requests
except ImportError:
    requests = None


class SensorCommunityUploader(BaseUploader):
    """
    Uploader za sensor.community
    - radi na principu buffera: skuplja merenja dok ne istekne interval
    - na isteku intervala pravi prosek i šalje 2 POST-a (PM i meteo)
    """

    def __init__(self, config: dict, device: dict):
        super().__init__(config, device)

        sc_cfg = config.get("external", {}).get("sensor_community", {})

        # da li je uključen
        self.enabled = sc_cfg.get("enabled", False)

        # endpoint koji je standardan za sensor.community
        self.base_url = sc_cfg.get(
            "base_url",
            "https://api.sensor.community/v1/push-sensor-data/",
        )

        # tvoj ID koji smo pravili tipa "pengy-wifi-00aabbcc"
        self.sensor_id = sc_cfg.get("sensor_id", "pengy-wifi-unknown")

        # interval u sekundama (npr. 145)
        self.interval = sc_cfg.get("interval_seconds", None)
        if self.interval is None:
            # fallback na minute ako neko ostavi staru postavku
            self.interval = sc_cfg.get("interval_minutes", 5) * 60

        # čisto da se vidi koji firmware šalje
        self.software_version = sc_cfg.get("software_version", "pengy-wifi-1.0")

        # interno stanje
        self.last_sent = 0  # epoch u sekundama
        self.buffer = []    # ovde čuvamo merenja dok ne istekne interval
        
        self._last_sent = None   # None | "pm" | "meteo"

    # ExternalManager prvo pita ovo, pa tek onda send()
    def is_enabled(self) -> bool:
        return self.enabled

    def should_send(self, now_ts: int) -> bool:
        if not self.enabled:
            return False
        return (now_ts - self.last_sent) >= self.interval

    def send(self, measurement: dict):
        """
        Poziva se iz ExternalManager-a SVAKI put kad stigne novo merenje.
        Mi ga samo dodamo u buffer, i ako je vreme - pošaljemo agregat.
        """
        
        # dodaj merenje u buffer
        if measurement:
            self.buffer.append(measurement)

        now_ts = int(time.time())
        if (now_ts - self.last_sent) < self.interval:
            # još nije vreme da se šalje
            return

        # vreme je da se pošalje – napravi prosek
        avg = self._average_buffer()
        if avg is None:
            # nema podataka, samo osveži vreme
            self.last_sent = now_ts
            return
        
        led_status.set_uploading()
        
        try:
            self._send_pm(avg)
            gc.collect()
            self._send_meteo(avg)
            gc.collect()
        except Exception as exc:
            # nećemo da puknemo glavni loop
            led_status.set_off()
            print("sensor.community send error:", exc)
        
        """
        # pošalji dva seta podataka
        try:
            if self._last_sent == "pm":
                sent = self._send_meteo(avg)
                if sent:
                    self._last_sent = "meteo"
                else:
                    # fallback na PM
                    sent = self._send_pm(avg)
                    if sent:
                        self._last_sent = "pm"
            else:
                sent = self._send_pm(avg)
                if sent:
                    self._last_sent = "pm"
                else:
                    sent = self._send_meteo(avg)
                    if sent:
                        self._last_sent = "meteo"
        except Exception as exc:
            # nećemo da puknemo glavni loop
            led_status.set_off()
            print("sensor.community send error:", exc)
        """

        # resetuj stanje
        self.buffer = []
        self.last_sent = now_ts
        
        led_status.set_off()

    # ------------------------------------------------------------------
    # Pomoćne metode
    # ------------------------------------------------------------------

    def _average_buffer(self):
        """
        Prolazi kroz sva merenja u bufferu i pravi prosek po ključevima.
        Uzimamo samo numeričke vrednosti.
        """
        if not self.buffer:
            return None

        # sakupi sve ključeve koji se javljaju
        all_keys = set()
        for m in self.buffer:
            all_keys.update(m.keys())

        result = {}
        count = len(self.buffer)

        for key in all_keys:
            total = 0.0
            valid_count = 0
            for m in self.buffer:
                val = m.get(key, None)
                if isinstance(val, (int, float)):
                    total += val
                    valid_count += 1
            if valid_count > 0:
                result[key] = total / valid_count

        return result

    def _send_pm(self, data: dict):
        """
        Šalje PM podatke (X-Pin: 1)
        Mapiramo razne moguće nazive iz tvog measurement-a.
        """
        pm1 = (
            data.get("pm1")
            or data.get("pm1_0")
            or data.get("pm01")
        )
        pm25 = (
            data.get("pm25")
            or data.get("pm2_5")
            or data.get("pm_2_5")
        )
        pm10 = (
            data.get("pm10")
            or data.get("pm10_0")   # 👈 dodali smo ovo
            or data.get("pm_10")
        )


        # ništa nema, nema smisla da šaljemo ovaj deo
        if pm1 is None and pm25 is None and pm10 is None:
            return False

        payload = {
            "software_version": self.software_version,
            "sensordatavalues": []
        }

        # SC očekuje:
        # P0 = PM1.0
        # P2 = PM2.5
        # P1 = PM10
        if pm1 is not None:
            payload["sensordatavalues"].append({
                "value_type": "P0",
                "value": str(round(pm1, 2))
            })
        if pm25 is not None:
            payload["sensordatavalues"].append({
                "value_type": "P2",
                "value": str(round(pm25, 2))
            })
        if pm10 is not None:
            payload["sensordatavalues"].append({
                "value_type": "P1",
                "value": str(round(pm10, 2))
            })

        # ako nema ništa u listi, nemoj da šalješ
        if not payload["sensordatavalues"]:
            return False

        self._post_json(payload, x_pin="1")
        print("PM2.5: ", pm25, "    PM10: ", pm10)
        return True

    def _send_meteo(self, data: dict):
        """
        Šalje temperaturu / vlagu / pritisak (X-Pin: 11)
        """
        temp = data.get("temperature") or data.get("temp")
        hum = data.get("humidity") or data.get("hum")
        press = data.get("pressure") or data.get("press")

        # ako nemamo nijedno od ovoga, ne šaljemo
        if temp is None and hum is None and press is None:
            return False

        # ako je pritisak u Pa (~100000), pretvori u hPa
        if press is not None and press > 2000:
            press = press / 100.0

        payload = {
            "software_version": self.software_version,
            "sensordatavalues": []
        }

        if temp is not None:
            payload["sensordatavalues"].append({
                "value_type": "temperature",
                "value": str(round(temp, 2))
            })

        if hum is not None:
            payload["sensordatavalues"].append({
                "value_type": "humidity",
                "value": str(round(hum, 2))
            })

        if press is not None:
            payload["sensordatavalues"].append({
                "value_type": "pressure",
                "value": str(round(press, 2))
            })

        if not payload["sensordatavalues"]:
            return False

        self._post_json(payload, x_pin="11")
        return True

    def _post_json(self, payload: dict, x_pin: str):
        """
        Zajednička metoda za slanje ka sensor.community.
        Koristimo urequests ako postoji.
        """
        
        if not self.enabled:
            return

        if self.base_url is None or self.sensor_id is None:
            return

        if not requests:
            # nema urequests na firmwaru
            print("SC disabled (no urequests)")
            return

        headers = {
            "Content-Type": "application/json",
            "X-Pin": x_pin,
            "X-Sensor": self.sensor_id,
        }

        print("SC POST", x_pin)
        
        resp = None
        try:
            gc.collect()
            body = ujson.dumps(payload)
            gc.collect()

            # sensor.community vraća 201 kad je ok
            resp = requests.post(
                self.base_url,
                headers=headers,
                data=body,
            )
            # ako hoćeš, ovde možeš da proveriš status
            print("SC RESP:", resp.status_code)
            resp.close()
        except Exception as exc:
            print("SC post error:", exc)
            pass
        finally:
            if resp:
                try: resp.close()
                except: pass
            body = None
            headers = None
            payload = None
            gc.collect()
