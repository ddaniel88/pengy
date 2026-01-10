# main.py (refactored for dual MQTT, secure OTA, clean structure)

import network
import socket
import time
import ujson
import machine
import ntptime
import led_status
import os
import gc
import aqi_utils

from net import mqtt_client
import offline_buffer
from maintenance_handler import MaintenanceHandler
from ota import ota_manager
from ota import ota_state
from sensors.manager import SensorManager
from sensors.factory import create_sensors
from uploader.external_manager import ExternalManager

try:
    from uploader.sensor_community import get_last_sc
except ImportError:
    get_last_sc = None


FIRMWARE_VERSION = "2025-11-21_02"
CONFIG_FILE = "config.json"
UNIX_EPOCH_OFFSET = 946684800  # seconds from 1970 → 2000

NTP_SERVERS = [
    "time.google.com",
    "time.cloudflare.com",
    "pool.ntp.org",
    "0.rs.pool.ntp.org",
    "1.rs.pool.ntp.org"
]

# RAM buffer for minute-level fallback
MINUTE_RAM_LIMIT = 30
minute_ram_buffer = []

# global state
config = None
device_meta = {}
sensor_manager = None
last_ntp_sync_ts = 0


# ---------------------------------------------------------------------------
# CONFIG & WIFI & TIME
# ---------------------------------------------------------------------------

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return ujson.loads(f.read())
    except Exception:
        return None

def wait_for_network_ready(timeout_seconds: int = 30) -> bool:
    """
    Čeka da:
    - STA connected
    - ifconfig ima IP/GW/DNS != 0.0.0.0
    - DNS resolve radi (getaddrinfo)
    """
    import network, socket, time

    sta = network.WLAN(network.STA_IF)
    start = time.ticks_ms()

    while time.ticks_diff(time.ticks_ms(), start) < timeout_seconds * 1000:
        if not sta.isconnected():
            time.sleep_ms(300)
            continue

        ip, mask, gw, dns = sta.ifconfig()
        if ip != "0.0.0.0" and gw != "0.0.0.0" and dns != "0.0.0.0":
            try:
                #socket.getaddrinfo("test.mosquitto.org", 1883)
                socket.getaddrinfo("time.google.com", 123)
                return True
            except OSError:
                pass

        time.sleep_ms(400)

    return False

def disable_wifi_powersave(sta):
    # Različiti MicroPython build-ovi imaju različite API-je.
    # Cilj: ne rušiti program, a isključiti PS gde može.
    try:
        # Neki portovi koriste keyword pm
        sta.config(pm=0)
        print("[WiFi] Power-save OFF (pm=0)")
        return True
    except Exception as e1:
        try:
            # Neki koriste powersave flag
            sta.config(powersave=False)
            print("[WiFi] Power-save OFF (powersave=False)")
            return True
        except Exception as e2:
            print("[WiFi] Power-save setting unsupported:", e1, "|", e2)
            return False


def _time_is_set() -> bool:
    # na MicroPython-u, ako NTP nije setovan, time.time() je obično mali (od 2000 epoch)
    # koristi se UNIX_EPOCH_OFFSET pa je ok da se proveri samo raw time.time()
    return time.time() > 100000

def sync_time_utc_once(host: str) -> bool:
    import socket
    try:
        socket.setdefaulttimeout(5)
    except AttributeError:
        pass

    ntptime.host = host
    try:
        ntptime.settime()
        print("[NTP] synced via", host)
        return True
    except Exception as exc:
        print("[NTP] failed via", host, ":", exc)
        return False

def ensure_time_synced(min_interval_s: int = 3600, force: bool = False) -> bool:
    """
    Pozovi pre publish-a / pre SC upload-a.
    - ne blokira boot
    - ne zove NTP prečesto
    """
    global last_ntp_sync_ts

    now = time.time()
    if not force and last_ntp_sync_ts and (now - last_ntp_sync_ts) < min_interval_s:
        return _time_is_set()

    if not force and _time_is_set():
        # vreme je već setovano, samo osvežavaj po intervalu
        last_ntp_sync_ts = now
        return True

    hosts = NTP_SERVERS
    for h in hosts:
        if sync_time_utc_once(h):
            last_ntp_sync_ts = time.time()
            return True

    return False


def connect_wifi(ssid: str, password: str, timeout_seconds: int = 20):
    sta = network.WLAN(network.STA_IF)
    sta.active(True)

    disable_wifi_powersave(sta)

    try:
        sta.disconnect()
    except:
        pass

    if not sta.isconnected():
        try:
            sta.connect(ssid, password)
        except Exception as exc:
            print("WiFi connect error:", exc)
            return sta, False

        start = time.time()
        while not sta.isconnected() and (time.time() - start) < timeout_seconds:
            time.sleep(1)

    return sta, sta.isconnected()


def ensure_wifi_connected(wifi_cfg, sta) -> bool:
    """
    Non-blocking runtime WiFi reconnect:
    - samo jedan pokušaj, bez sleep/backoff spirale
    """
    ssid = wifi_cfg.get("ssid")
    password = wifi_cfg.get("password")

    if not ssid:
        return False

    if sta.isconnected():
        return True

    print("[WiFi] Disconnected → reconnecting (kick)...")

    try:
        sta.disconnect()
    except:
        pass

    try:
        sta.connect(ssid, password)
    except Exception as exc:
        print("[WiFi] connect error:", exc)
        return False

    # kratko čekanje da uhvati link
    start = time.time()
    while not sta.isconnected() and (time.time() - start) < 8:
        time.sleep_ms(250)

    ok = sta.isconnected()
    print("[WiFi] Reconnect:", "OK" if ok else "FAIL")

    if ok:
        wait_for_network_ready(timeout_seconds=10)

    return ok

# ---------------------------------------------------------------------------
# STATUS HTTP SERVER
# ---------------------------------------------------------------------------

def start_status_server():
    addr = socket.getaddrinfo("0.0.0.0", 8080)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    s.settimeout(0.1)
    print("Status server listening on", addr)
    return s


def handle_status_request(sock):
    global config, sensor_manager, device_meta

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
            body = ujson.dumps({
                "supported_fields": sensor_manager.get_supported_fields() if sensor_manager else [],
                "device": device_meta,
                "last_sc": get_last_sc() if get_last_sc else None
            })
            client.send(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n")
            client.send(body)

        elif "GET /config" in req_txt:
            client.send(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n")
            client.send(ujson.dumps(config))

        elif "GET /info" in req_txt:
            now = time.time()
            unix_ts = now + UNIX_EPOCH_OFFSET
            info = {
                "fw": FIRMWARE_VERSION,
                "device": device_meta.get("uid"),
                "geo": {
                    "lat": device_meta.get("lat"),
                    "lon": device_meta.get("lon"),
                    "altitude": device_meta.get("altitude"),
                    "ground_offset": device_meta.get("ground_offset"),
                },
                "streams": device_meta.get("streams", []),
                "ts": unix_ts
            }
            client.send(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n")
            client.send(ujson.dumps(info))

        else:
            client.send(b"HTTP/1.0 404 Not Found\r\n\r\n")

    except Exception as exc:
        print("Status server error:", exc)
    finally:
        client.close()
        gc.collect()


# ---------------------------------------------------------------------------
# MINUTE RAM BUFFER
# ---------------------------------------------------------------------------

def add_minute_to_ram(topic: str, payload: str):
    global minute_ram_buffer
    minute_ram_buffer.append((topic, payload))
    if len(minute_ram_buffer) > MINUTE_RAM_LIMIT:
        minute_ram_buffer.pop(0)


def flush_minute_ram(mqtt_client_instance, cfg):
    global minute_ram_buffer
    if not minute_ram_buffer:
        return mqtt_client_instance

    still = []
    for topic, payload in minute_ram_buffer:
        mqtt_client_instance, ok, net_err, _ = mqtt_client.publish(
            mqtt_client_instance,
            {"mqtt": cfg},
            topic.encode(),
            payload,
            retain=False,
            message_type="minute",
            from_flush=True,
        )
        if not ok and net_err:
            still.append((topic, payload))

    minute_ram_buffer = still
    return mqtt_client_instance

# ---------------------------------------------------------------------------
# MQTT INITIALIZATION (dual client support)
# ---------------------------------------------------------------------------

def init_mqtt_clients(config, sensor_manager, uid):
    """
    Connect-on-demand:
    - ne konektujemo MQTT na boot-u
    - samo pripremimo config + runtime state
    """
    mqtt_clients = []

    def add_section(name):
        cfg = config.get(name, {})
        if not cfg.get("enabled", False):
            print(name, "disabled.")
            return
        mqtt_clients.append({
            "name": name,
            "cfg": cfg,
            "client": None,
            "next_retry_ts": 0,
            "cooldown_s": 15,
        })

    add_section("mqtt_primary")
    add_section("mqtt_secondary")

    # OTA broker selection (bez konekcije)
    ota_slot = None
    sec_opts = config.get("security", {})
    require_secure_ota = sec_opts.get("require_secure_ota", False)

    for entry in mqtt_clients:
        cfg = entry["cfg"]
        if not cfg.get("allow_ota_commands", False):
            continue
        if require_secure_ota and not cfg.get("tls", False):
            print("[OTA] Ignoring", entry["name"], "because secure OTA required.")
            continue

        base_topic = cfg.get("base_topic", "pengy/rs/nis")
        cmd_topic = "{}/{}/cmd/#".format(base_topic, uid)

        ota_slot = {
            "name": entry["name"],
            "cfg": cfg,
            "client": None,
            "subscribed": False,
            "maintenance": None,
            "cmd_topic": cmd_topic,
            "next_retry_ts": 0,
            "cooldown_s": 30,   # OTA reconnect cadence
        }

        print("[OTA] Will use", entry["name"], "for OTA commands.")
        break

    return mqtt_clients, ota_slot


# ---------------------------------------------------------------------------
# MQTT PUBLISH HELPERS
# ---------------------------------------------------------------------------

def publish_to_all(mqtt_clients, ota_slot, config, sensor_manager, uid, payload_dict, msg_type):
    payload = ujson.dumps(payload_dict)

    for entry in mqtt_clients:
        cfg = entry["cfg"]
        topic = "{}/{}/{}".format(cfg["base_topic"], uid, msg_type)

        client, ok, net_err, did_reconnect = _mqtt_publish_entry(
            entry,
            uid,
            topic,
            payload,
            retain=cfg.get("retain", False),
            msg_type=msg_type,
            from_flush=False
        )

        if not ok and net_err:
            print("[MQTT] Failed on", entry["name"], "net err")
            if msg_type == "minute":
                add_minute_to_ram(topic, payload)

        # ako je ovo OTA broker, posle reconnect-a obavezno resubscribe
        if ota_slot and entry["name"] == ota_slot["name"]:
            ota_slot["client"] = client
            if did_reconnect:
                ota_slot["subscribed"] = False
            _ensure_ota_subscribed(ota_slot, config, sensor_manager, uid)


def _mqtt_publish_entry(entry, uid, topic, payload, retain, msg_type, from_flush=False):
    """
    entry: dict {cfg, client, next_retry_ts, cooldown_s}
    Vraca updated client i ok flag.
    """
    sta = network.WLAN(network.STA_IF)
    if not sta.isconnected():
        return entry["client"], False, False, False
    
    now = time.time()

    if entry["next_retry_ts"] and now < entry["next_retry_ts"]:
        return entry["client"], False, False, False

    client, ok, net_err, did_reconnect = mqtt_client.publish(
        entry["client"],
        {"mqtt": entry["cfg"]},
        topic.encode(),
        payload,
        retain=retain,
        message_type=msg_type,
        from_flush=from_flush
    )

    entry["client"] = client

    if net_err:
        entry["next_retry_ts"] = now + entry.get("cooldown_s", 15)
    else:
        entry["next_retry_ts"] = 0

    return client, ok, net_err, did_reconnect


def _ensure_ota_subscribed(ota_slot, config, sensor_manager, uid):
    """
    Posle reconnect-a ili prvog connect-a:
    - napravi MaintenanceHandler ako treba
    - subscribe na cmd topic
    """
    if not ota_slot or not ota_slot.get("client"):
        return

    if ota_slot.get("maintenance") is None:
        base_topic = ota_slot["cfg"].get("base_topic", "pengy/rs/nis")
        ota_slot["maintenance"] = MaintenanceHandler(
            ota_slot["client"],
            config,
            sensor_manager,
            base_topic,
            uid
        )

    if not ota_slot.get("subscribed"):
        ok = mqtt_client.setup_downlink(
            ota_slot["client"],
            ota_slot["cmd_topic"],
            ota_slot["maintenance"].handle_command
        )
        ota_slot["subscribed"] = bool(ok)


# ---------------------------------------------------------------------------
# MINUTE SAMPLING + AQI LED
# ---------------------------------------------------------------------------

def perform_minute_sampling(sensor_manager, samples_per_min, trim_extremes, uid, mqtt_clients, config, external_mgr, now):
    """
    Radi jedan minut merenja (samples_per_min merenja).
    Vraća:
        minute_data_payload (dict)
    """

    measurement = sensor_manager.measure_minute_all(
        samples_count=samples_per_min,
        interval_seconds=2,
        trim_extremes=trim_extremes
    )

    pm25 = (
        measurement.get("pm25") or measurement.get("pm2_5") or measurement.get("pm_2_5")
    )
    pm10 = (
        measurement.get("pm10") or measurement.get("pm10_0") or measurement.get("pm_10")
    )

    # LED AQI
    aqi = aqi_utils.get_aqi_level(pm25=pm25, pm10=pm10)
    led_status.set_aqi_level(aqi)
    time.sleep(1)

    # normalize
    supported = sensor_manager.get_supported_fields()
    normalized = {}
    for key in supported:
        if key in measurement and measurement[key] is not None:
            normalized[key] = measurement[key]

    ensure_time_synced(min_interval_s=3600, force=False)

    payload = {
        "device": uid,
        "ts": now,
        "data": normalized
    }

    # SC upload
    try:
        external_mgr.send_all(measurement)
    except Exception as exc:
        print("[External] Sensor.Community error:", exc)

    return payload, pm25, pm10


# ---------------------------------------------------------------------------
# AGGREGATION
# ---------------------------------------------------------------------------

def calculate_aggregated(measurements, agg_window):
    if not measurements:
        return None

    all_keys = set()
    for m in measurements:
        if m:
            all_keys.update(m.keys())

    agg = {}
    for k in all_keys:
        values = [m[k] for m in measurements if m and isinstance(m.get(k), (int, float))]
        agg[k] = sum(values) / len(values) if values else None

    now = time.time() + UNIX_EPOCH_OFFSET
    return {
        "ts": now,
        "minutes": agg_window,
        "data": agg
    }

# ---------------------------------------------------------------------------
# OTA CYCLE HANDLER
# ---------------------------------------------------------------------------

def handle_ota_cycle(maintenance, config):
    """
    Proverava da li je OTA zatražen i pokreće update.
    Ako OTA uspe → machine.reset() i ova funkcija se ne vraća.
    """
    if not maintenance:
        return

    requested, ota_data = maintenance.consume_ota_request()
    if not requested:
        return

    print("[OTA] Update requested → running OTA")
    led_status.set_mode("OTA_IN_PROGRESS")

    ok = ota_manager.start_update(config)

    if ok:
        # OTA manager će resetovati uređaj.
        return

    print("[OTA] Update failed")
    try:
        led_status.set_mode("OTA_ERROR")
    except:
        pass


# ---------------------------------------------------------------------------
# BUFFER FLUSH HANDLER
# ---------------------------------------------------------------------------

def flush_all_buffers(mqtt_clients, config):
    """
    Flush file-based offline buffer + RAM minute buffer.
    """
    for entry in mqtt_clients:
        cfg = entry["cfg"]

        # file buffer flush
        client = entry["client"]
        client, _ = offline_buffer.flush_buffer(
            client,
            {"mqtt": cfg},
            mqtt_client.publish,
        )
        entry["client"] = client

        # RAM minute flush (ako ga koristiš)
        entry["client"] = flush_minute_ram(entry["client"], cfg)


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------

def main():
    global config, device_meta, sensor_manager

    print("Pengy boot (refactored dual-MQTT version)")
    led_status.set_boot()

    # Load config
    config = load_config()
    if not config:
        print("Config missing → entering setup mode")
        import setup
        setup.run_setup_mode()
        return

    # WiFi
    wifi_cfg = config.get("wifi", {})
    ssid = wifi_cfg.get("ssid")
    password = wifi_cfg.get("password")

    sta, wifi_ok = connect_wifi(ssid, password)
    print("WiFi:", "OK" if wifi_ok else "FAIL")

    if wifi_ok:
        wait_for_network_ready(timeout_seconds=15)

    # Device meta
    device_meta = config.get("device", {})
    uid = device_meta.get("uid", "unknown")

    # Sensors
    sensors = create_sensors(device_meta)
    sensor_manager = SensorManager(sensors)

    # MQTT initialize
    mqtt_clients, ota_slot = init_mqtt_clients(config, sensor_manager, uid)

    # External uploader (sensor.community...)
    external_mgr = ExternalManager(config, device_meta)

    # Status HTTP server
    status_srv = start_status_server()

    # Sampling config
    sampling_cfg = config.get("sampling", {})
    samples_per_min = sampling_cfg.get("samples_per_min", 5)
    trim_extremes = sampling_cfg.get("trim", True)
    agg_window = sampling_cfg.get("agg_window", 10)

    minute_measurements = []
    last_minute_ts = time.time() + UNIX_EPOCH_OFFSET
    last_flush_ts = last_minute_ts

    # OTA TRY_UPDATE → mark success
    state = ota_state.load_state()
    if state.get("state") == ota_state.STATE_TRY_UPDATE:
        ota_state.mark_successful(FIRMWARE_VERSION)
    
    WIFI_COOLDOWN_S = 15
    wifi_next_retry_ts = 0

    OTA_PROBE_S = 30
    ota_next_probe_ts = 0
    
    wifi_fail_count = 0

    # -----------------------------------------------------------------------
    # MAIN LOOP
    # -----------------------------------------------------------------------
    while True:
        # ---------------------------
        # WiFi health check (every 10s)
        # ---------------------------
        now_raw = time.time()
        
        if not sta.isconnected():
            if now_raw >= wifi_next_retry_ts:
                ok = ensure_wifi_connected(wifi_cfg, sta)
                if ok:
                    wifi_fail_count = 0
                else:
                    wifi_fail_count += 1
                wifi_next_retry_ts = now_raw + WIFI_COOLDOWN_S
        else:
            wifi_next_retry_ts = 0
            wifi_fail_count = 0
            
        if wifi_fail_count >= 5:
            print("[WiFi] Hard reset STA interface")
            try:
                sta.active(False)
                time.sleep_ms(500)
                sta.active(True)
                disable_wifi_powersave(sta)
                wifi_fail_count = 0
                wifi_next_retry_ts = now_raw + 5
            except Exception as exc:
                print("[WiFi] Hard reset failed:", exc)
        
        now = now_raw + UNIX_EPOCH_OFFSET

        # WiFi LED status
        led_status.set_wifi_fail_mode(not sta.isconnected())
        led_status.tick()

        # Handle HTTP status server
        handle_status_request(status_srv)

        # --- OTA MQTT keepalive / probe ---
        if ota_slot and not ota_slot.get("client"):
            # probaj povremeno da se poveže (da OTA radi i kad nema publish-a)
            if sta.isconnected() and now_raw >= ota_next_probe_ts:
                ota_next_probe_ts = now_raw + OTA_PROBE_S
                if now_raw >= ota_slot["next_retry_ts"]:
                    print("[OTA] probe connect...")
                    c = mqtt_client.connect_mqtt({"mqtt": ota_slot["cfg"]})
                    if c:
                        ota_slot["client"] = c
                        ota_slot["subscribed"] = False
                        ota_slot["next_retry_ts"] = 0
                        _ensure_ota_subscribed(ota_slot, config, sensor_manager, uid)
                    else:
                        ota_slot["next_retry_ts"] = now_raw + ota_slot.get("cooldown_s", 30)

        # MQTT downlink (OTA)
        if ota_slot and ota_slot.get("client"):
            try:
                ota_slot["client"].check_msg()
            except:
                # ako pukne, pusti da publish/probe ponovo uspostavi
                try:
                    ota_slot["client"].disconnect()
                except:
                    pass
                ota_slot["client"] = None
                ota_slot["subscribed"] = False

        # OTA cycle
        maintenance = ota_slot["maintenance"] if ota_slot else None
        handle_ota_cycle(maintenance, config)

        # ---------------------------
        # Minute sampling
        # ---------------------------
        if now - last_minute_ts >= 60:
            led_status.set_measuring()
            payload, pm25, pm10 = perform_minute_sampling(
                sensor_manager,
                samples_per_min,
                trim_extremes,
                uid,
                mqtt_clients,
                config,
                external_mgr,
                now
            )

            # Publish minute to all MQTT brokers
            publish_to_all(mqtt_clients, ota_slot, config, sensor_manager, uid, payload, "minute")

            print("Sent minute:", "PM2.5", pm25, "PM10", pm10)

            # Aggregation buffer
            minute_measurements.append(payload["data"])
            if len(minute_measurements) >= agg_window:
                agg = calculate_aggregated(minute_measurements, agg_window)
                if agg:
                    agg_payload = {
                        "device": uid,
                        "ts": agg["ts"],
                        "minutes": agg_window,
                        "data": agg["data"]
                    }
                    publish_to_all(mqtt_clients, ota_slot, config, sensor_manager, uid, agg_payload, "agg")

                minute_measurements = []

            last_minute_ts = now

        # ---------------------------
        # FLUSH BUFFERS every 30 sec
        # ---------------------------
        if now - last_flush_ts >= 30:
            flush_all_buffers(mqtt_clients, config)
            last_flush_ts = now

        # Restore LED if needed
        if led_status.is_off():
            led_status.restore()

        time.sleep_ms(300)


# ---------------------------------------------------------------------------
# AUTOSTART
# ---------------------------------------------------------------------------

try:
    main()
except Exception as exc:
    print("FATAL ERROR:", exc)
    machine.reset()
