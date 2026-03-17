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

def _stage(name):
    # Best-effort stage marker for SC crash forensics.
    if not pdiag:
        return
    try:
        pdiag.set_stage(name)
    except Exception:
        pass

def _is_oserror(exc):
    return isinstance(exc, OSError)

def _errno(exc):
    try:
        if hasattr(exc, "args") and exc.args:
            return exc.args[0]
    except Exception:
        pass
    return None

def _is_network_not_ready(exc):
    # ESP32 often raises OSError(-202) while WiFi says "connected"
    return _is_oserror(exc) and _errno(exc) == -202

def _wifi_kick():
    try:
        import network
        sta = network.WLAN(network.STA_IF)
        if sta.active():
            try:
                sta.disconnect()
            except Exception:
                pass
    except Exception:
        pass

def _tcp_probe(url: str, timeout_s=2):
    """
    Quick TCP probe before entering requests.post().
    Helps avoid hard hangs inside HTTP/socket path.
    """
    s = None
    try:
        target = url
        if target.startswith("https://"):
            target = target[len("https://"):]
            default_port = 443
        elif target.startswith("http://"):
            target = target[len("http://"):]
            default_port = 80
        else:
            default_port = 80

        host = target.split("/", 1)[0]
        if ":" in host:
            host, port_txt = host.split(":", 1)
            port = int(port_txt)
        else:
            port = default_port

        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        try:
            s.settimeout(timeout_s)
        except Exception:
            pass
        s.connect(addr)
        return True
    except OSError as exc:
        if _is_network_not_ready(exc):
            _wifi_kick()
        return False
    except Exception:
        return False
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass
    

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
        pm4 = (
            data.get("pm4")
            or data.get("pm04")
            or data.get("pm4_0")
            or data.get("pm_4")
            or data.get("pm_4_0")
            or data.get("pm4.0")
        )

        if pm1 is None and pm25 is None and pm10 is None and pm4 is None:
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
        if pm4 is not None:
            payload["sensordatavalues"].append({"value_type": "P4", "value": str(round(pm4, 2))})

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
            _stage("SC_POST_BEGIN_" + str(x_pin))
            gc.collect()
            body = ujson.dumps(payload)
            gc.collect()
            _stage("SC_POST_BODY_READY_" + str(x_pin))
            
            # Defensive: prevent SC POST from blocking indefinitely (WDT killer)
            try:
                _stage("SC_POST_TIMEOUT_CFG_" + str(x_pin))
                sc_cfg = self.config.get("external", {}).get("sensor_community", {})
                timeout_s = int(sc_cfg.get("socket_timeout_s", 5) or 5)
                socket.setdefaulttimeout(timeout_s)
            except Exception:
                timeout_s = 5

            _stage("SC_POST_TCP_PROBE_BEGIN_" + str(x_pin))
            if not _tcp_probe(self.base_url, timeout_s=min(timeout_s, 2)):
                _stage("SC_POST_TCP_PROBE_FAIL_" + str(x_pin))
                try:
                    if pdiag:
                        pdiag.set_net_fail("sc_tcp_probe:" + str(x_pin), 113)
                except Exception:
                    pass
                return False
            _stage("SC_POST_TCP_PROBE_OK_" + str(x_pin))

            _stage("SC_POST_CALL_BEGIN_" + str(x_pin))
            print("SC POST", x_pin)
            resp = requests.post(self.base_url, headers=headers, data=body)
            
            _stage("SC_POST_CALL_OK_" + str(x_pin))
            code = getattr(resp, "status_code", None)
            _stage("SC_POST_STATUS_" + str(x_pin) + "_" + str(code))
            print("SC RESP:", code)

            if code == 403:
                self._saw_403_in_cycle = True

            # SC tipično vraća 201 kad je ok
            ok = (code == 201) or (code == 200)

            try:
                _stage("SC_POST_CLOSE_BEGIN_" + str(x_pin))
                resp.close()
                _stage("SC_POST_CLOSE_OK_" + str(x_pin))
            except:
                pass

            return ok

        except Exception as exc:
            _stage("SC_POST_EXC_" + str(x_pin))
            print("SC post error:", exc)
            
            try:
                if pdiag and isinstance(exc, OSError):
                    errno = exc.args[0] if hasattr(exc, "args") and exc.args else None
                    pdiag.set_net_fail("sc_post:" + str(x_pin), errno)
                    if _is_network_not_ready(exc):
                        _wifi_kick()
            except Exception:
                pass
            
            return False

        finally:
            if resp:
                try:
                    _stage("SC_POST_FINALLY_CLOSE_BEGIN_" + str(x_pin))
                    resp.close()
                    _stage("SC_POST_FINALLY_CLOSE_OK_" + str(x_pin))
                except:
                    pass
            gc.collect()
            _stage("SC_POST_FINALLY_DONE_" + str(x_pin))
