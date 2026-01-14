# uploader/external_manager.py
import time
from uploader.sensor_community import SensorCommunityUploader

class ExternalManager:
    def __init__(self, config: dict, device: dict, include_sc: bool = True):
        self.config = config
        self.device = device
        self.uploaders = []

        external_cfg = config.get("external", {})

        # sensor.community (optional)
        sc_cfg = external_cfg.get("sensor_community", {})
        if include_sc and sc_cfg.get("enabled", False):
            self.uploaders.append(SensorCommunityUploader(config, device))

        # pengy api
        pengy_cfg = external_cfg.get("pengy_api", {})
        if pengy_cfg.get("enabled", False):
            from uploader.pengy_api import PengyApiUploader
            self.uploaders.append(PengyApiUploader(config, device))

    def send_all(self, measurement: dict):
        now_ts = int(time.time())
        for u in self.uploaders:
            try:
                # zadržavamo kompatibilnost: neki uploader-i mogu imati should_send,
                # ali snapshot SC više nije vezan ovde kad include_sc=False
                if hasattr(u, "should_send"):
                    if u.should_send(now_ts):
                        u.send(measurement)
                else:
                    u.send(measurement)
            except Exception as exc:
                print("external send error:", exc)
