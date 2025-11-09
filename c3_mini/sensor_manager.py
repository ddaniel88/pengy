# sensor_manager.py

class SensorManager:
    """
    Prima listu senzora (BaseEnvSensor) i za svaki uzme merenje,
    pa ih spoji u jedan dict.
    """

    def __init__(self, sensors):
        self.sensors = sensors or []

    def get_supported_fields(self):
        all_fields = set()
        for s in self.sensors:
            try:
                all_fields.update(s.get_supported_fields())
            except Exception:
                pass
        return list(all_fields)

    def measure_minute_all(self, samples_count=5, interval_seconds=1, trim_extremes=True):
        result = {}
        for s in self.sensors:
            m = s.measure_one_minute(samples_count, interval_seconds, trim_extremes)
            if m:
                # jednostavno lepljenje – kasnije možemo da rešavamo konflikte
                result.update(m)
        return result
