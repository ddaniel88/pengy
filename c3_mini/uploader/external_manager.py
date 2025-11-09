# uploader/external_manager.py
import time
from uploader.sensor_community import SensorCommunityUploader

class ExternalManager:
    def __init__(self, config: dict, device: dict):
        self.config = config
        self.device = device
        self.uploaders = []

        external_cfg = config.get("external", {})

        # dodaj senzorsku zajednicu ako je uključena
        sc_cfg = external_cfg.get("sensor_community", {})
        if sc_cfg.get("enabled", False):
            self.uploaders.append(SensorCommunityUploader(config, device))

    def send_all(self, measurement: dict):
        now_ts = int(time.time())
        for u in self.uploaders:
            try:
                if u.should_send(now_ts):
                    u.send(measurement)
            except Exception as exc:
                print("external send error:", exc)
