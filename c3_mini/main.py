# main.py
import network
import socket
import time
import ujson
import machine
import ntptime
import led_status

from sensor_sen55 import Sen55Sensor
from sensor_manager import SensorManager
import mqtt_client
import wifi_setup
import offline_buffer
from uploader.external_manager import ExternalManager
from maintenance_handler import MaintenanceHandler
try:
    from uploader.sensor_community import get_last_sc
except ImportError:
    get_last_sc = None

led_status.set_boot()

CONFIG_FILE = "config.json"

UNIX_EPOCH_OFFSET = 946684800  # sekundi od 1970 do 2000

# RAM buffer za minutne
MINUTE_RAM_LIMIT = 30
minute_ram_buffer = []

# globali za status
last_minute_measurement = None
previous_minute_measurement = None
last_aggregated_measurement = None
minute_measurements_buffer = []
device_meta = {}
sensor_manager = None
mqtt_client_instance = None
config = None

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return ujson.loads(f.read())
    except:
        return None

def sync_time_utc():
    try:
        ntptime.host = "pool.ntp.org"
        ntptime.settime()
        print("NTP time synced (UTC).")
    except Exception as exc:
        print("NTP sync failed:", exc)

def connect_wifi(ssid: str, password: str, timeout_seconds: int = 15) -> bool:
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        sta.connect(ssid, password)
        start = time.time()
        while not sta.isconnected() and (time.time() - start) < timeout_seconds:
            time.sleep(1)
    return sta.isconnected()

def start_status_server():
    addr = socket.getaddrinfo("0.0.0.0", 8080)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    s.settimeout(0.1)
    print("Status server on", addr)
    return s

def handle_status_request(sock):
    global last_minute_measurement, previous_minute_measurement, last_aggregated_measurement, sensor_manager, device_meta
    try:
        client, addr = sock.accept()
    except OSError:
        return

    try:
        req = client.recv(512)
        try:
            req_txt = req.decode("utf-8")
        except:
            req_txt = req.decode("utf-8", "ignore")

        if "GET /status" in req_txt:
            status_obj = {
                "last_minute": last_minute_measurement,
                "prev_minute": previous_minute_measurement,
                "last_agg": last_aggregated_measurement,
                "supported_fields": sensor_manager.get_supported_fields() if sensor_manager else [],
                "device": device_meta
            }
            
            if get_last_sc:
                status_obj["last_sc"] = get_last_sc()
            
            body = ujson.dumps(status_obj)
            client.send(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n")
            client.send(body)
        elif "GET /config" in req_txt:
            body = ujson.dumps(config)
            client.send(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n")
            client.send(body)
        elif "GET /info" in req_txt:
            now = time.time()
            unix_ts = now + UNIX_EPOCH_OFFSET
            
            info = {
                "fw": device_meta.get("version"),
                "device": device_meta.get("uid"),
                "geo": {
                    "lat": device_meta.get("lat"),
                    "lon": device_meta.get("lon"),
                    "altitude": device_meta.get("altitude"),
                },
                "streams": device_meta.get("streams", []),
                "ts": unix_ts
            }
            client.send(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n")
            client.send(ujson.dumps(info))
        else:
            client.send(b"HTTP/1.0 404 Not Found\r\n\r\n")
    except Exception as exc:
        print("status handler error:", exc)
    finally:
        client.close()

def calculate_aggregated(measurements):
    if not measurements:
        return None

    all_keys = set()
    for m in measurements:
        if m:
            all_keys.update(m.keys())

    meta_fields = set()

    agg = {}
    for k in all_keys:
        if k in meta_fields:
            continue
        vals = []
        for m in measurements:
            if not m:
                continue
            v = m.get(k)
            if isinstance(v, (int, float)):
                vals.append(v)
        agg[k] = sum(vals) / len(vals) if vals else None

    now = time.time()
    unix_ts = now + UNIX_EPOCH_OFFSET
    
    agg["minutes"] = len(measurements)
    agg["ts"] = unix_ts
    return agg

def on_mqtt_cmd(topic, msg):
    # za sada samo ispiši
    print("MQTT CMD:", topic, msg)
    # ovde ćemo kasnije: reboot, force upload, pull update...

def add_minute_to_ram(topic: str, payload: str):
    global minute_ram_buffer
    minute_ram_buffer.append((topic, payload))
    if len(minute_ram_buffer) > MINUTE_RAM_LIMIT:
        # FIFO – izbaci najstariju
        minute_ram_buffer.pop(0)

def flush_minute_ram(mqtt_client_instance, config):
    global minute_ram_buffer
    if not minute_ram_buffer:
        return mqtt_client_instance

    still_pending = []
    for topic, payload in minute_ram_buffer:
        mqtt_client_instance, ok, net_err = mqtt_client.publish(
            mqtt_client_instance,
            config,
            topic.encode(),
            payload,
            False,
            0,
            "minute",
            from_flush=True,
        )
        if not ok and net_err:
            # mrežni problem – ostavi za kasnije
            still_pending.append((topic, payload))
        # ako nije mrežni, bacamo – minute nas ne zanimaju trajno

    minute_ram_buffer = still_pending
    return mqtt_client_instance

def main():
    global last_minute_measurement, previous_minute_measurement, minute_measurements_buffer
    global last_aggregated_measurement, sensor_manager, mqtt_client_instance, config, device_meta

    config = load_config()
    if not config:
        print("No config, entering setup mode...")
        wifi_setup.run_setup_mode()
        return

    wifi_ok = connect_wifi(config["wifi"]["ssid"], config["wifi"]["password"])
    if not wifi_ok:
        print("Cannot connect to WiFi, entering setup mode...")
        led_status.set_wifi_fail()
        wifi_setup.run_setup_mode()
        return

    print("WiFi connected.")
    led_status.set_ok()
    sync_time_utc()

    # device meta i config
    device_meta = config.get("device", {})
    base_topic = config.get("mqtt", {}).get("base_topic", "pengy/rs/nis")
    uid = device_meta.get("uid", "unknown")
    
    # senzori
    sen55 = Sen55Sensor()
    sensor_manager = SensorManager([sen55])

    # MQTT
    mqtt_client_instance = mqtt_client.connect_mqtt(config)
    
    # odmah probaj da isprazniš offline fajl (ako je bilo restarta bez neta)
    mqtt_client_instance, _ = offline_buffer.flush_buffer(
        mqtt_client_instance,
        config,
        mqtt_client.publish,
    )
    # minute iz RAM-a nemamo posle restarta, to je ok

    # maintenance
    maintenance = None
    if mqtt_client_instance:
        maintenance = MaintenanceHandler(
            mqtt_client_instance,
            config,
            sensor_manager,
            base_topic,
            uid
        )
        cmd_topic = "{}/{}/cmd/#".format(base_topic, uid)
        mqtt_client.setup_downlink(
            mqtt_client_instance,
            cmd_topic,
            maintenance.handle_command
        )

    status_server = start_status_server()

    sampling_cfg = config.get("sampling", {})
    samples_per_minute = sampling_cfg.get("samples_per_min", 5)
    trim_extremes = sampling_cfg.get("trim", True)
    aggregation_window = sampling_cfg.get("agg_window", 10)

    # uploader
    external_mgr = ExternalManager(config, device_meta)

    minute_counter = 0
    now = time.time()
    unix_ts = now + UNIX_EPOCH_OFFSET
    last_minute_ts = unix_ts
    last_flush_ts = unix_ts

    while True:
        now = time.time() + UNIX_EPOCH_OFFSET

        # HTTP
        handle_status_request(status_server)

        # MQTT downlink
        if mqtt_client_instance:
            try:
                mqtt_client_instance.check_msg()
            except:
                pass

        if now - last_minute_ts >= 60:
            print("⏱ minute", minute_counter)
            led_status.set_measuring()
            measurement = sensor_manager.measure_minute_all(
                samples_count=samples_per_minute,
                interval_seconds=1,
                trim_extremes=trim_extremes
            )
            led_status.set_ok()

            previous_minute_measurement = last_minute_measurement
            
            supported = sensor_manager.get_supported_fields()
            normalized = {}
            for key in supported:
                normalized[key] = measurement.get(key) if measurement and key in measurement else None
            
            # standardizovan payload za MQTT
            payload = {
                "device": uid,
                "ts": now,
                "fw": device_meta.get("version"),
                "geo": {
                    "lat": device_meta.get("lat"),
                    "lon": device_meta.get("lon"),
                    "altitude": device_meta.get("altitude"),
                },
                "data": normalized or {}
            }
            last_minute_measurement = payload

            # publish na MQTT
            if mqtt_client_instance:
                minute_topic = "{}/{}/minute".format(base_topic, uid)
                mqtt_client_instance, ok, net_err = mqtt_client.publish(
                    mqtt_client_instance,
                    config,
                    minute_topic.encode(),
                    ujson.dumps(payload),
                    retain=False,
                    message_type="minute",
                )
                if not ok and net_err:
                    # čuvamo samo ako je mreža, i samo u RAM
                    add_minute_to_ram(minute_topic, ujson.dumps(payload))

            # external send (sensor.community...)
            if measurement:
                external_mgr.send_all(measurement)

            # agregacija
            if measurement:
                minute_measurements_buffer.append(measurement)
                if len(minute_measurements_buffer) >= aggregation_window:
                    last_aggregated_measurement = calculate_aggregated(minute_measurements_buffer)
                    # po želji: publish i agg
                    if mqtt_client_instance and last_aggregated_measurement:
                        agg_topic = "{}/{}/agg".format(base_topic, uid)
                        
                        mqtt_client_instance, ok, net_err = mqtt_client.publish(
                            mqtt_client_instance,
                            config,
                            agg_topic.encode(),
                            ujson.dumps(last_aggregated_measurement),
                            retain=False,
                            message_type="aggregate",
                        )
                        # ovde NIŠTA ne radimo – mqtt_client će sam upisati u fajl
                        # ako je mreža pala
                    minute_measurements_buffer = []

            last_minute_ts = now
            minute_counter += 1

        now = time.time() + UNIX_EPOCH_OFFSET

        # npr. na svakih 30 sekundi
        if now - last_flush_ts >= 30:
            mqtt_client_instance, _ = offline_buffer.flush_buffer(
                mqtt_client_instance,
                config,
                mqtt_client.publish,
            )
            mqtt_client_instance = flush_minute_ram(mqtt_client_instance, config)
            last_flush_ts = now

        time.sleep_ms(150)

# auto-start
main()
