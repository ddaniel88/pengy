# maintenance_handler.py

import machine
import time
import ujson


class MaintenanceHandler:
    """
    Obrada "maintenance" komandi koje stižu preko MQTT-a.

    Podržane komande (JSON poruka sa poljem "op"):

    - {"op": "reboot"}
    - {"op": "force_measure"}
    - {"op": "ota"}  # može i sa dodatnim poljima, npr {"op": "ota", "version": "latest"}

    OTA se ne izvršava ovde direktno, već se samo podiže zastavica
    (self.ota_requested == True), a main petlja odlučuje kada će da pozove
    run_ota_update().
    """

    def __init__(self, mqtt_client, config, sensor_manager, base_topic, uid):
        self.mqtt = mqtt_client          # raw MQTTClient instanca (umqtt.simple.MQTTClient)
        self.config = config
        self.sensor_manager = sensor_manager
        self.base_topic = base_topic
        self.uid = uid

        # OTA stanje
        self.ota_requested = False
        self._ota_payload = None  # ovde možemo da čuvamo npr. {"version": "..."} ako zatreba

    # --------------------------------------------------------------------- helpers

    def _safe_publish(self, topic_str, payload_obj):
        """
        Pojednostavljena publish logika samo za maintenance poruke.
        Ne koristi offline buffer, ovo su "best effort" komande / odgovori.
        """
        if not self.mqtt:
            print("MQTT client is None, cannot publish.")
            return

        try:
            topic = topic_str.encode()
            payload = ujson.dumps(payload_obj)
            self.mqtt.publish(topic, payload)
            print("Published maintenance message to", topic_str)
        except Exception as exc:
            print("Failed to publish maintenance message:", exc)

    # --------------------------------------------------------------------- public API

    def handle_command(self, topic, msg):
        """
        MQTT callback:
        - topic: bytes
        - msg: bytes (JSON sa "op")
        """
        try:
            data = ujson.loads(msg)
        except Exception as exc:
            print("❌ Error decoding command JSON:", exc, "raw msg:", msg)
            return

        op = data.get("op")
        print("🔧 Command:", op)

        if op == "reboot":
            self._handle_reboot()

        elif op == "force_measure":
            self._handle_force_measure()

        elif op == "ota":
            self._handle_ota(data)

        else:
            print("⚠️ Unknown command op:", op)

    def consume_ota_request(self):
        """
        Main petlja može periodično da pita:
            requested, payload = maintenance.consume_ota_request()
        Ako je requested == True, payload je originalni JSON (dict) komande.
        Zastavica se resetuje (one-shot).
        """
        if self.ota_requested:
            self.ota_requested = False
            return True, (self._ota_payload or {})
        return False, None

    # --------------------------------------------------------------------- op handlers

    def _handle_reboot(self):
        print("🔄 Rebooting on request...")
        # kratki delay da poruka stigne do logova
        time.sleep(1)
        machine.reset()

    def _handle_force_measure(self):
        print("📡 Force measure requested")
        try:
            measurement = self.sensor_manager.measure_minute_all()
        except Exception as exc:
            print("Force measure failed:", exc)
            return

        if not measurement:
            print("Force measure returned empty measurement.")
            measurement = {}

        payload = {
            "device": self.uid,
            "ts": time.time(),
            "data": measurement,
        }

        topic_str = f"{self.base_topic}/{self.uid}/minute"
        self._safe_publish(topic_str, payload)

    def _handle_ota(self, data):
        # ovde kasnije možemo da čitamo npr. data.get("version"), data.get("url"), itd.
        print("🚀 OTA requested via MQTT")
        self.ota_requested = True
        self._ota_payload = data
