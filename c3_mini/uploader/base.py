# uploader/base.py

class BaseUploader:
    def __init__(self, config: dict, device: dict):
        self.config = config
        self.device = device

    def is_enabled(self) -> bool:
        return True

    def should_send(self, now_ts: int) -> bool:
        return True

    def send(self, measurement: dict):
        raise NotImplementedError
