# maintenance_handler.py
import machine
import time
import ujson
import os
import network

class MaintenanceHandler:
    def __init__(self, mqtt_client, config, sensor_manager, base_topic, uid):
        self.mqtt = mqtt_client
        self.config = config
        self.sensor_manager = sensor_manager
        self.base_topic = base_topic
        self.uid = uid

    def handle_command(self, topic, msg):
        try:
            data = ujson.loads(msg)
            op = data.get("op")
            print("🔧 Command:", op)

            if op == "reboot":
                print("🔄 Rebooting...")
                time.sleep(1)
                machine.reset()

            elif op == "force_measure":
                print("📡 Force measure requested")
                measurement = self.sensor_manager.measure_minute_all()
                payload = {
                    "device": self.uid,
                    "ts": time.time(),
                    "data": measurement
                }
                topic = f"{self.base_topic}/{self.uid}/minute".encode()
                self.mqtt.publish(topic, ujson.dumps(payload))

            elif op == "pull_update":
                # očekujemo da dođe i "target": "main" | "sensor_community" | "pulse_eco"
                # i "url": "http://...."
                target = data.get("target")
                url = data.get("url")
                print("⬇️ OTA requested for", target, "from", url)
                # ovde ćeš ti kasnije uraditi HTTP GET i snimiti u update_*.py

            elif op == "wifi_reset":
                print("📶 Reset WiFi config")
                if "config.json" in os.listdir():
                    os.remove("config.json")
                sta = network.WLAN(network.STA_IF)
                sta.active(False)
                machine.reset()

            elif op == "factory_reset":
                print("⚠️ Factory reset initiated")
                for f in ["config.json", "update_main.py"]:
                    try:
                        os.remove(f)
                    except:
                        pass
                machine.reset()

            else:
                print("⚠️ Unknown command:", op)

        except Exception as e:
            print("❌ Error processing command:", e)
