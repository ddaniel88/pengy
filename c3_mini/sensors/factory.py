# sensors/factory.py
from sensors.sen55 import Sen55Sensor
from sensors.sps30 import Sps30Sensor
from sensors.bme280 import Bme280Sensor

# mapiranje string -> klasa
SENSOR_REGISTRY = {
    "sen55": Sen55Sensor,
    "sps30": Sps30Sensor,
    "bme280": Bme280Sensor,
}


def create_sensors(device_cfg: dict) -> list:
    """
    device_cfg – deo iz config.json, npr:
      "device": {
        "uid": "...",
        "lat": ...,
        "sensors": ["sen55", "bme280"]
      }
    """
    enabled = device_cfg.get("sensors") or []

    # ako nije ništa zadato, podrazumevaj sve
    if not enabled:
        enabled = list(SENSOR_REGISTRY.keys())

    sensors = []
    for name in enabled:
        cls = SENSOR_REGISTRY.get(name)
        if cls is not None:
            try:
                sensors.append(cls())
            except Exception as ex:
                print("Failed to init sensor", name, "->", ex)

    return sensors
