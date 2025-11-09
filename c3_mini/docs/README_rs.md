# C3 Mini Sistem za Merenje Kvaliteta Vazduha

## Pregled
Ovaj projekat predstavlja kompletan firmware za uređaj zasnovan na **WEMOS C3 Mini v2.1.0** i **Sensirion SEN55 SDN-T** senzorima.
Uređaj meri parametre kvaliteta vazduha (PM1.0, PM2.5, PM4.0, PM10, temperaturu, vlažnost, VOC, NOx) i samostalno šalje podatke putem **MQTT-a**.
U potpunosti funkcioniše bez posredničkog servera — dovoljan je MQTT broker.

Cilj projekta je da bude **otvoren i dostupan svima**: za edukaciju, lokalne inicijative i unapređenje kvaliteta života kroz praćenje vazduha.

## Glavne mogućnosti
- Automatski Wi-Fi setup preko Access Point moda i web forme
- Merenje svih dostupnih SEN55 parametara
- Slanje podataka putem MQTT u formatu:

  ```text
  pengy/<država>/<grad>/<uid>/minute
  pengy/<država>/<grad>/<uid>/agg
  ```

  Na primer: `pengy/rs/nis/69bafab7/minute`

- Standardizovan JSON payload (uvek ista struktura, čak i kad vrednosti nisu dostupne)
- HTTP status server: `/status`, `/config`, `/info`
- Više senzora istovremeno (modularna arhitektura)
- OTA ažuriranje (`main.py`, `sensor_community.py`, `pulse_eco.py`)
- Komande putem MQTT-a (`reboot`, `pull_update`, `factory_reset`, `wifi_reset`)
- Logička izolacija po modulima: `maintenance_handler`, `mqtt_client`, `uploader/…`

## Struktura projekta
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

## MQTT topici
- Publish: `pengy/rs/nis/<uid>/minute`
- Publish: `pengy/rs/nis/<uid>/agg`
- Subscribe: `pengy/rs/nis/<uid>/cmd/#`

## Primer payload-a
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

## OTA sistem
`boot.py` proverava i primenjuje:
- `update_main.py` → `main.py`
- `update_sensor_community.py` → `uploader/sensor_community.py`
- `update_pulse_eco.py` → `uploader/pulse_eco.py`

## Misija
Projekat je open-source i namenjen svima koji žele da doprinesu transparentnom merenju kvaliteta vazduha.
Svaka stanica je samostalna i može slati podatke direktno javnim platformama, kao i lokalnim zajednicama.
