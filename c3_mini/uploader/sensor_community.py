# uploader/sensor_community.py
import time
import ujson
import gc
import socket
from uploader.base import BaseUploader

try:
    import urequests as requests
except ImportError:
    requests = None
    
try:
    import pengy_diag as pdiag
except Exception:
    pdiag = None


class SensorCommunityUploader(BaseUploader):
    """
    Uploader za sensor.community (snapshot mode)
    - nema buffer/average
    - nema internal timer
    - send(measurement) pokušava odmah da pošalje poslednje merenje
    - retry jednom po ciklusu ako POST ne uspe
    """

    def __init__(self, config: dict, device: dict):
        super().__init__(config, device)

        sc_cfg = config.get("external", {}).get("sensor_community", {})

        self.enabled = sc_cfg.get("enabled", False)
        self.base_url = sc_cfg.get(
            "base_url",
            "https://api.sensor.community/v1/push-sensor-data/",
        )
        self.sensor_id = sc_cfg.get("sensor_id", "pengy-wifi-unknown")
        self.software_version = sc_cfg.get("software_version", "pengy-wifi-1.0")

        # cycle flags (used to suppress retry on hard failures like HTTP 403)
        self._saw_403_in_cycle = False

    def is_enabled(self) -> bool:
        return self.enabled

    def send(self, measurement: dict) -> bool:
        """
        Pošalji snapshot (poslednje merenje).
        Vraca True ako je bar jedan POST uspeo (PM ili meteo).
        Ako ne uspe, pokuša još jednom i odustaje.
        """
        if not self.enabled:
            return False

        if not measurement:
            return False

        if not requests:
            print("SC disabled (no urequests)")
            return False

        # reset per-send cycle flags
        self._saw_403_in_cycle = False

        # 1st attempt
        ok = self._send_both(measurement)
        if ok:
            return True

        # no retry for hard auth/registration errors
        if self._saw_403_in_cycle:
            print("SC 403 -> no retry this cycle")
            return False

        # retry once
        time.sleep_ms(250)
        gc.collect()
        ok2 = self._send_both(measurement)
        return bool(ok2)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _send_both(self, data: dict) -> bool:
        """
        Šalje PM (X-Pin 1) i meteo (X-Pin 11) ako ima vrednosti.
        True ako je bar jedan POST uspeo.
        """
        pm_ok = self._send_pm(data)
        gc.collect()
        met_ok = self._send_meteo(data)
        gc.collect()
        return bool(pm_ok or met_ok)

    def _send_pm(self, data: dict) -> bool:
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
            or data.get("pm10_0")
            or data.get("pm_10")
        )

        if pm1 is None and pm25 is None and pm10 is None:
            return False

        payload = {
            "software_version": self.software_version,
            "sensordatavalues": []
        }

        # P0 = PM1.0, P2 = PM2.5, P1 = PM10
        if pm1 is not None:
            payload["sensordatavalues"].append({"value_type": "P0", "value": str(round(pm1, 2))})
        if pm25 is not None:
            payload["sensordatavalues"].append({"value_type": "P2", "value": str(round(pm25, 2))})
        if pm10 is not None:
            payload["sensordatavalues"].append({"value_type": "P1", "value": str(round(pm10, 2))})

        if not payload["sensordatavalues"]:
            return False

        ok = self._post_json(payload, x_pin="1")
        if ok:
            print("SC PM OK | PM2.5:", pm25, "PM10:", pm10)
        return ok

    def _send_meteo(self, data: dict) -> bool:
        temp = data.get("temperature") or data.get("temp")
        hum = data.get("humidity") or data.get("hum")
        press = data.get("pressure") or data.get("press")

        if temp is None and hum is None and press is None:
            return False

        if press is not None and press > 2000:
            press = press / 100.0

        payload = {
            "software_version": self.software_version,
            "sensordatavalues": []
        }

        if temp is not None:
            payload["sensordatavalues"].append({"value_type": "temperature", "value": str(round(temp, 2))})
        if hum is not None:
            payload["sensordatavalues"].append({"value_type": "humidity", "value": str(round(hum, 2))})
        if press is not None:
            payload["sensordatavalues"].append({"value_type": "pressure", "value": str(round(press, 2))})

        if not payload["sensordatavalues"]:
            return False

        ok = self._post_json(payload, x_pin="11")
        if ok:
            print("SC METEO OK")
        return ok

    def _post_json(self, payload: dict, x_pin: str) -> bool:
        if not self.enabled:
            return False
        if self.base_url is None or self.sensor_id is None:
            return False
        if not requests:
            return False

        headers = {
            "Content-Type": "application/json",
            "X-Pin": x_pin,
            "X-Sensor": self.sensor_id,
        }

        resp = None
        try:
            gc.collect()
            body = ujson.dumps(payload)
            gc.collect()
            
            # Defensive: prevent SC POST from blocking indefinitely (WDT killer)
            try:
                sc_cfg = self.config.get("external", {}).get("sensor_community", {})
                timeout_s = int(sc_cfg.get("socket_timeout_s", 5) or 5)
                socket.setdefaulttimeout(timeout_s)
            except Exception:
                pass

            print("SC POST", x_pin)
            resp = requests.post(self.base_url, headers=headers, data=body)
            code = getattr(resp, "status_code", None)
            print("SC RESP:", code)

            if code == 403:
                self._saw_403_in_cycle = True

            # SC tipično vraća 201 kad je ok
            ok = (code == 201) or (code == 200)

            try:
                resp.close()
            except:
                pass

            return ok

        except Exception as exc:
            print("SC post error:", exc)
            
            try:
                if pdiag and isinstance(exc, OSError):
                    errno = exc.args[0] if hasattr(exc, "args") and exc.args else None
                    pdiag.set_net_fail("sc_post:" + str(x_pin), errno)
            except Exception:
                pass
            
            return False

        finally:
            if resp:
                try:
                    resp.close()
                except:
                    pass
            gc.collect()
