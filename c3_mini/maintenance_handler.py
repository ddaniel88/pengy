# maintenance_handler.py
import ujson
import gc
import machine
import time


class MaintenanceHandler:
    """
    Obrada maintenance komandi pristiglih preko MQTT-a.

    Očekuje JSON payload oblika:
    {
        "op": "ota" | "reboot" | "ping" | ...,
        "token": "...",       # za OTA komandu (obavezno ako postoji security.ota_token)
        ... ostala polja ...
    }

    OTA logika:
    - Poštuje:
        security.ota_enabled (bool, default True)
        security.ota_token (string ili None)
      secure_ota (TLS) je već proverio main() preko require_secure_ota + tls.
    """

    def __init__(self, mqtt_client, config, sensor_manager, base_topic, uid):
        self.mqtt_client = mqtt_client
        self.config = config
        self.sensor_manager = sensor_manager
        self.base_topic = base_topic
        self.uid = uid

        # OTA state
        self.ota_requested = False
        self._ota_payload = None

    # ----------------------------------------------------------------------
    # PUBLIC API
    # ----------------------------------------------------------------------

    def handle_command(self, topic, msg):
        """
        MQTT callback. Topic je bytes, msg je bytes.
        """
        try:
            try:
                payload_str = msg.decode("utf-8")
            except Exception:
                payload_str = msg.decode("utf-8", "ignore")

            gc.collect()

            data = ujson.loads(payload_str)
        except Exception as exc:
            print("[Maintenance] Invalid JSON in command:", exc)
            return

        op = data.get("op")
        if not op:
            print("[Maintenance] Command without 'op' field, ignoring.")
            return

        op = str(op).lower()
        print("[Maintenance] CMD op =", op, "data =", data)

        if op == "ota":
            self._handle_ota(data)
        elif op == "reboot":
            self._handle_reboot()
        elif op == "ping":
            self._handle_ping()
        else:
            print("[Maintenance] Unknown op:", op)

    def consume_ota_request(self):
        """
        Glavni loop zove ovo da vidi da li je OTA tražen.
        Vraća: (requested: bool, ota_payload: dict|None)
        Ako requested == True, handler resetuje svoj interni flag.
        """
        if not self.ota_requested:
            return False, None

        payload = self._ota_payload
        self.ota_requested = False
        self._ota_payload = None
        return True, payload

    # ----------------------------------------------------------------------
    # INTERNAL HANDLERS
    # ----------------------------------------------------------------------

    def _handle_ota(self, data):
        """
        OTA komanda – poštuje security flagove iz config-a:
        security.ota_enabled (default True)
        security.ota_token (ako postoji, komanda mora da ima isti 'token')

        require_secure_ota je već ispoštovan u main.py preko izbora MQTT endpointa
        (init_mqtt_clients).
        """
        security_cfg = self.config.get("security", {})

        ota_enabled = security_cfg.get("ota_enabled", True)
        if not ota_enabled:
            print("[OTA] Ignoring OTA – security.ota_enabled = False")
            return

        expected_token = security_cfg.get("ota_token", None)
        if expected_token:
            msg_token = data.get("token")
            if not msg_token or str(msg_token) != str(expected_token):
                print("[OTA] Token mismatch or missing. Ignoring OTA.")
                return

        print("🚀 [OTA] OTA requested via MQTT (security checks passed)")
        self.ota_requested = True
        self._ota_payload = data

    def _handle_reboot(self):
        print("[Maintenance] Reboot requested – rebooting device.")
        time.sleep(0.5)
        machine.reset()

    def _handle_ping(self):
        """
        Jednostavna 'ping' komanda – za sada samo ispiše log.
        Po želji kasnije možeš da dodaš odgovor preko MQTT-a.
        """
        print("[Maintenance] Ping received.")
