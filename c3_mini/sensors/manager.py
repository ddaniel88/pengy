# sensors/manager.py

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

    def measure_minute_all(self, samples_count=10, interval_seconds=2, trim_extremes=True):
        result = {}
        for s in self.sensors:
            try:
                m = s.measure_one_minute(samples_count, interval_seconds, trim_extremes)
            except Exception as ex:
                try:
                    name = s.__class__.__name__
                except Exception:
                    name = 'sensor'
                print('[SENSORS] measure failed for', name, '->', ex)
                m = None
            if m:
                # jednostavno lepljenje – kasnije možemo da rešavamo konflikte
                result.update(m)
        return result

