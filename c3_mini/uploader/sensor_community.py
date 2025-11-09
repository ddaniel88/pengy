# uploader/sensor_community.py
# Ovo je samo skelet – ovde će ići konkretan format kad odlučimo koji endpoint koriste.
import time
import ujson
import usocket

from uploader.base import BaseUploader

class SensorCommunityUploader(BaseUploader):
    def __init__(self, config, device):
        super().__init__(config, device)
        self.last_sent = 0
        self.interval = config.get("external", {}).get("sensor_community", {}).get("interval_minutes", 10) * 60

    def should_send(self, now_ts: int) -> bool:
        return (now_ts - self.last_sent) >= self.interval

    def send(self, measurement: dict):
        # placeholder – ovde ćemo napraviti stvarni payload za sensor.community
        # za sada samo ispiši
        print("Pretvaram se da šaljem na sensor.community:", measurement)
        self.last_sent = int(time.time())
