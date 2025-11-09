# C3 Mini Environmental Sensor System

## Overview
This project is a complete firmware for **WEMOS C3 Mini v2.1.0** paired with a **Sensirion SEN55 SDN-T** sensor.
It measures air quality parameters (PM1.0, PM2.5, PM4.0, PM10, temperature, humidity, VOC, NOx) and sends data over **MQTT**.
It works standalone — a MQTT broker is enough.

The goal is to make it **open and reusable** for communities and individuals who want transparent air-quality data.

## Key Features
- Automatic Wi-Fi setup via AP + HTML form
- Full SEN55 measurement support
- MQTT data publishing in the form:

  ```text
  pengy/<country>/<city>/<uid>/minute
  pengy/<country>/<city>/<uid>/agg
  ```

  Example: `pengy/rs/nis/69bafab7/minute`

- Standardized JSON payload (same shape every time, even with nulls)
- HTTP status server: `/status`, `/config`, `/info`
- Multi-sensor ready (via SensorManager)
- OTA updates (`main.py`, `sensor_community.py`, `pulse_eco.py`)
- MQTT downlink commands (`reboot`, `pull_update`, `factory_reset`, `wifi_reset`)
- Modular architecture

## Project Structure
```text
/
├── boot.py
├── main.py
├── config.json
├── sensor_base.py
├── sensor_sen55.py
├── sensor_manager.py
├── mqtt_client.py
├── maintenance_handler.py
├── wifi_setup.py
├── wifi_login.html
└── uploader/
    ├── base.py
    ├── external_manager.py
    ├── sensor_community.py
    └── pulse_eco.py
```

## MQTT Topics
- Publish: `pengy/rs/nis/<uid>/minute`
- Publish: `pengy/rs/nis/<uid>/agg`
- Subscribe: `pengy/rs/nis/<uid>/cmd/#`

## Sample Payload
```json
{
  "device": "69bafab7",
  "ts": 1731210000,
  "fw": "1.0.0",
  "geo": { "lat": 43.319, "lon": 21.896, "altitude": 210 },
  "data": {
    "pm1_0": 18.2,
    "pm2_5": 19.1,
    "pm4_0": null,
    "pm10": 20.4,
    "temperature": 26.7,
    "humidity": 45.5,
    "voc_index": null,
    "nox_index": null
  }
}
```

## OTA
`boot.py` applies:
- `update_main.py` → `main.py`
- `update_sensor_community.py` → `uploader/sensor_community.py`
- `update_pulse_eco.py` → `uploader/pulse_eco.py`

## Mission
This project is an open-source public-good effort to enable transparent air-quality monitoring.
Each station is independent and can send data directly to public platforms or local community servers.
