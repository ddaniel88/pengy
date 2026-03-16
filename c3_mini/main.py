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

import pengy_diag as pdiag

from net import mqtt_client
import offline_buffer
from maintenance_handler import MaintenanceHandler
from ota import ota_manager
from ota import ota_state
from sensors.manager import SensorManager
from sensors.factory import create_sensors
from uploader.external_manager import ExternalManager
from uploader.sensor_community import SensorCommunityUploader

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

_rtc = machine.RTC()

# ---------------------------------------------------------------------------
# WATCHDOG (WDT)
# ---------------------------------------------------------------------------

def init_watchdog(cfg: dict):
    """Best-effort WDT init. Returns wdt or None."""
    try:
        wcfg = (cfg or {}).get("watchdog", {})
        enabled = wcfg.get("enabled", True)
        if not enabled:
            print("[WDT] disabled by config")
            return None

        timeout_ms = int(wcfg.get("timeout_ms", 120_000) or 120_000)

        # Some ports expose machine.WDT, some don't.
        wdt = machine.WDT(timeout=timeout_ms)
        print("[WDT] enabled, timeout_ms =", timeout_ms)
        return wdt
    except Exception as exc:
        print("[WDT] unavailable/failed:", exc)
        return None


def wdt_feed(wdt):
    try:
        if wdt:
            wdt.feed()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# RTC (diagnostics)
# ---------------------------------------------------------------------------
# We keep these wrapper functions so the rest of main.py stays unchanged.
# Implementation is in pengy_diag.py. RTC usage can be disabled there.

def diag_set_stage(stage: str):
    pdiag.set_stage(stage)

def diag_get_stage():
    return pdiag.get_stage()

def diag_clear():
    # Backwards-compatible: previously wiped entire RTC.
    # Now we only clear crash info (stage is still useful).
    pdiag.clear_crash()

# ---------------------------------------------------------------------------
# CONFIG & WIFI & TIME
# ---------------------------------------------------------------------------

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return ujson.loads(f.read())
    except Exception:
        return None

def wait_for_network_ready(timeout_seconds: int = 30, wdt=None) -> bool:
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
        wdt_feed(wdt)
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


def connect_wifi(ssid: str, password: str, timeout_seconds: int = 20, wdt=None):
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
            wdt_feed(wdt)
            time.sleep(1)

    return sta, sta.isconnected()


def ensure_wifi_connected(wifi_cfg, sta, wdt=None) -> bool:
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
        wdt_feed(wdt)
        time.sleep_ms(250)

    ok = sta.isconnected()
    print("[WiFi] Reconnect:", "OK" if ok else "FAIL")

    if ok:
        diag_set_stage("WAIT_NET_READY_RECONNECT")
        wait_for_network_ready(timeout_seconds=10, wdt=wdt)
        diag_set_stage("IDLE")

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
        diag_set_stage("FLUSH_RAM_MINUTE_PUBLISH_BEGIN")
        mqtt_client_instance, ok, net_err, _ = mqtt_client.publish(
            mqtt_client_instance,
            {"mqtt": cfg},
            topic.encode(),
            payload,
            retain=False,
            message_type="minute",
            from_flush=True,
        )
        if ok:
            diag_set_stage("FLUSH_RAM_MINUTE_PUBLISH_OK")
        elif net_err:
            diag_set_stage("FLUSH_RAM_MINUTE_PUBLISH_NET_ERR")
        else:
            diag_set_stage("FLUSH_RAM_MINUTE_PUBLISH_FAIL")

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

def publish_to_all(mqtt_clients, ota_slot, config, sensor_manager, uid, payload_dict, msg_type, wdt=None):
    payload = ujson.dumps(payload_dict)
    ok_any = False  # True if at least one broker publish succeeded

    for entry in mqtt_clients:
        wdt_feed(wdt)
        diag_set_stage("MQTT_PUBLISH_" + msg_type.upper())
        cfg = entry["cfg"]
        topic = "{}/{}/{}".format(cfg["base_topic"], uid, msg_type)

        diag_set_stage("MQTT_PUBLISH_CALL_" + msg_type.upper())

        diag_set_stage("MQTT_BEFORE_PUBLISH_" + msg_type.upper())

        client, ok, net_err, did_reconnect = _mqtt_publish_entry(
            entry,
            uid,
            topic,
            payload,
            retain=cfg.get("retain", False),
            msg_type=msg_type,
            from_flush=False
        )
        
        diag_set_stage("MQTT_AFTER_PUBLISH_" + msg_type.upper())
        
        ok_any = ok_any or ok

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
    return ok_any


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

    # External uploaders (npr. Pengy API). SC je izdvojen i NE ide ovde.
    try:
        external_mgr.send_all(normalized)
    except Exception as exc:
        print("[External] uploader error:", exc)
    
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
        diag_set_stage("FLUSH_FILE_BUFFER_BEGIN")
        client = entry["client"]
        client, _ = offline_buffer.flush_buffer(
            client,
            {"mqtt": cfg},
            mqtt_client.publish,
        )
        entry["client"] = client
        diag_set_stage("FLUSH_FILE_BUFFER_DONE")

        # RAM minute flush (ako ga koristiš)
        diag_set_stage("FLUSH_RAM_MINUTE_BEGIN")
        entry["client"] = flush_minute_ram(entry["client"], cfg)
        diag_set_stage("FLUSH_RAM_MINUTE_DONE")


# ---------------------------------------------------------------------------
# SC BUILD AVERAGE
# ---------------------------------------------------------------------------

def _sc_pick(m: dict, keys):
    """Pick first numeric value; ignore 0.0 (treat as invalid)."""
    for k in keys:
        v = m.get(k)
        if isinstance(v, (int, float)) and v != 0.0:
            return v
    return None

def sc_accumulate(acc_sum: dict, acc_cnt: dict, m: dict):
    """Accumulate canonical fields used by SensorCommunityUploader."""
    if not m:
        return

    # PM (canonical keys for your uploader)
    pm1 = _sc_pick(m, ["pm1", "pm1_0", "pm01"])
    pm25 = _sc_pick(m, ["pm25", "pm2_5", "pm_2_5"])
    pm10 = _sc_pick(m, ["pm10", "pm10_0", "pm_10"])
    pm4 = _sc_pick(m, ["pm4", "pm4_0", "pm_4", "pm_4_0", "pm4.0"])

    # Meteo (common)
    temp = _sc_pick(m, ["temperature", "temp"])
    hum = _sc_pick(m, ["humidity", "hum"])
    pres = _sc_pick(m, ["pressure", "press"])

    def add(key, val):
        if val is None:
            return
        acc_sum[key] = acc_sum.get(key, 0.0) + float(val)
        acc_cnt[key] = acc_cnt.get(key, 0) + 1

    add("pm1", pm1)
    add("pm25", pm25)
    add("pm10", pm10)
    add("pm4", pm4)
    add("temperature", temp)
    add("humidity", hum)
    add("pressure", pres)

def sc_build_avg(acc_sum: dict, acc_cnt: dict) -> dict:
    """Return averaged dict (same shape your uploader expects), or None."""
    if not acc_cnt:
        return None

    out = {}

    def avg(key):
        c = acc_cnt.get(key, 0)
        if c <= 0:
            return None
        return acc_sum.get(key, 0.0) / c

    # Only include if present
    for k in ("pm1", "pm25", "pm10", "pm4", "temperature", "humidity", "pressure"):
        v = avg(k)
        if v is not None:
            out[k] = v

    # If nothing meaningful, skip
    return out if out else None

def sc_reset(acc_sum: dict, acc_cnt: dict):
    acc_sum.clear()
    acc_cnt.clear()


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------

def main():
    global config, device_meta, sensor_manager

    led_status.set_startup_white()
    
    reset_cause = machine.reset_cause()
    last_stage = diag_get_stage()
    last_stage_name = last_stage.get("stage") if isinstance(last_stage, dict) else None

    last_stage_ms = None
    try:
        if isinstance(last_stage, dict) and last_stage.get("ts") is not None:
            delta_ms = int((time.time() - int(last_stage["ts"])) * 1000)

            # Guard against invalid clock jumps after reboot.
            if 0 <= delta_ms <= 7 * 24 * 60 * 60 * 1000:
                last_stage_ms = delta_ms
            else:
                last_stage_ms = None
    except Exception:
        last_stage_ms = None
    
    diag_set_stage("BOOT_START")
    
    diag_sent = False

    # Load config
    config = load_config()
    if not config:
        print("Config missing → entering setup mode")
        import setup
        setup.run_setup_mode()
        return
        
    periodic_diag_last_ts = 0
    PERIODIC_DIAG_S = int(config.get("recovery", {}).get("periodic_diag_s", 600) or 600)  # 10 min
    
    # SAFE MODE after WDT reset: reduce network activity so device can stabilize
    SAFE_MODE_S = int(config.get("recovery", {}).get("safe_mode_after_wdt_s", 600) or 600)
    safe_mode_until = 0

    if reset_cause == 3:
        # WDT reset
        safe_mode_until = time.time() + SAFE_MODE_S
        print("[SAFE] WDT reset detected → safe mode for", SAFE_MODE_S, "s")

    boot_ts = time.time()
    BOOT_QUIET_S = int(config.get("power", {}).get("boot_quiet_s", 20) or 20)
    
    diag_next_try_ts = 0
    DIAG_RETRY_S = 10

    # Watchdog (reset if we truly hang)
    wdt = init_watchdog(config)

    diag_set_stage("BOOT_WIFI_CONNECT")
    # WiFi
    wifi_cfg = config.get("wifi", {})
    ssid = wifi_cfg.get("ssid")
    password = wifi_cfg.get("password")

    sta, wifi_ok = connect_wifi(ssid, password, wdt=wdt)
    print("WiFi:", "OK" if wifi_ok else "FAIL")

    if wifi_ok:
        diag_set_stage("WAIT_NET_READY_BOOT")
        wait_for_network_ready(timeout_seconds=15, wdt=wdt)
        diag_set_stage("IDLE")

    # Device meta
    device_meta = config.get("device", {})
    uid = device_meta.get("uid", "unknown")

    # Sensors
    diag_set_stage("BOOT_INIT_SENSORS")
    sensors = create_sensors(device_meta)
    sensor_manager = SensorManager(sensors)

    # MQTT initialize
    diag_set_stage("BOOT_INIT_MQTT")
    mqtt_clients, ota_slot = init_mqtt_clients(config, sensor_manager, uid)

    # External uploaders (without Sensor.Community, SC is independent)
    diag_set_stage("BOOT_EXTERNAL_MANAGER")
    external_mgr = ExternalManager(config, device_meta, include_sc=False)

    diag_set_stage("BOOT_READY")

    # Sensor.Community (independent timer)
    sc_cfg = config.get("external", {}).get("sensor_community", {})
    sc_enabled = bool(sc_cfg.get("enabled", False))
    try:
        sc_interval_s = int(sc_cfg.get("interval_seconds", 145))
    except Exception:
        sc_interval_s = 145
    if sc_interval_s <= 0:
        sc_interval_s = 145
    sc_uploader = SensorCommunityUploader(config, device_meta) if sc_enabled else None
    last_sc_ts = 0
    last_measurement_for_sc = None
    sc_sum = {}
    sc_cnt = {}
    sc_pending_avg = None
    sc_next_retry_ts = 0
    sc_retry_interval_s = int(sc_cfg.get("retry_interval_seconds", 20) or 20)
    if sc_retry_interval_s <= 0:
        sc_retry_interval_s = 20
    sc_pending_tries = 0
    try:
        sc_retry_max = int(sc_cfg.get("retry_max", 2))
    except Exception:
        sc_retry_max = 2
    if sc_retry_max < 0:
        sc_retry_max = 0

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

    # Sensor fail recovery
    sensor_fail_streak = 0
    SENSOR_FAIL_THRESHOLD = int(config.get('recovery', {}).get('sensor_fail_threshold', 3) or 3)

    # -----------------------------------------------------------------------
    # MAIN LOOP
    # -----------------------------------------------------------------------
    while True:
        try:
            # WDT feed (keep this very early)
            wdt_feed(wdt)

            # ---------------------------
            # WiFi health check (every 10s)
            # ---------------------------
            now_raw = time.time()
            
            in_safe_mode = (safe_mode_until and time.time() < safe_mode_until)
        
            if not sta.isconnected():
                if now_raw >= wifi_next_retry_ts:
                    diag_set_stage("WIFI_RECONNECT")
                    ok = ensure_wifi_connected(wifi_cfg, sta, wdt=wdt)
                    diag_set_stage("IDLE")
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
                    diag_set_stage("WIFI_HARD_RESET_STA")
                    sta.active(False)
                    time.sleep_ms(500)
                    sta.active(True)
                    disable_wifi_powersave(sta)
                    wifi_fail_count = 0
                    wifi_next_retry_ts = now_raw + 5
                    diag_set_stage("IDLE")
                except Exception as exc:
                    print("[WiFi] Hard reset failed:", exc)
        
            now = now_raw + UNIX_EPOCH_OFFSET

            # WiFi LED status
            led_status.set_wifi_fail_mode(not sta.isconnected())
            led_status.tick()

            # Handle HTTP status server
            handle_status_request(status_srv)
        
            # ---------------------------
            # BOOT DIAG (send ASAP after boot_quiet + wifi)
            # ---------------------------
            if not diag_sent:
                if (time.time() - boot_ts) >= BOOT_QUIET_S and sta.isconnected() and now_raw >= diag_next_try_ts:
                    diag_set_stage("MQTT_PUBLISH_DIAG")
                    prev_crash, prev_crash_count = pdiag.peek_crash()
                    
                    net_fail = None
                    try:
                        net_fail = pdiag.get_net_fail()
                    except Exception:
                        net_fail = None
                    
                    diag_payload = {
                        "device": uid,
                        "ts": now,
                        "fw": FIRMWARE_VERSION,
                        "reset_cause": reset_cause,
                        "last_stage": last_stage.get("stage") if last_stage else None,
                        "last_stage_ms": last_stage_ms,
                        "mem_free": gc.mem_free(),
                        "wifi": sta.isconnected(),
                        "wifi_fail_count": wifi_fail_count,
                        "rssi": sta.status("rssi") if sta.isconnected() else None,
                        "uptime_ms": time.ticks_ms(),
                        "net_op": net_fail.get("op") if net_fail else None,
                        "net_errno": net_fail.get("errno") if net_fail else None,
                        "kind": "boot"
                    }
                    
                    # Include prev_crash only if it exists (avoid payload noise)
                    if prev_crash:
                        diag_payload["prev_crash"] = prev_crash
                        diag_payload["prev_crash_count"] = prev_crash_count
                    
                    ok_any = publish_to_all(mqtt_clients, ota_slot, config, sensor_manager, uid, diag_payload, "diag", wdt=wdt)

                    diag_next_try_ts = now_raw + DIAG_RETRY_S

                    # Send only once; if publish failed, retry later.
                    if ok_any:
                        diag_sent = True
                        if prev_crash:
                            pdiag.clear_crash()
                            
                        try:
                            pdiag.clear_net_fail()
                        except Exception:
                            pass
                        diag_set_stage("IDLE")

                elif now_raw >= diag_next_try_ts:
                    diag_next_try_ts = now_raw + DIAG_RETRY_S
            
            # ---------------------------
            # PERIODIC DIAG (every N seconds, and ASAP when net_fail exists)
            # ---------------------------
            try:
                net_fail = pdiag.get_net_fail()
            except Exception:
                net_fail = None

            should_send_periodic = False

            if sta.isconnected() and (time.time() - boot_ts) >= BOOT_QUIET_S and (not in_safe_mode):
                if net_fail:
                    # send soon after a network failure to capture errno/op without waiting for reboot
                    should_send_periodic = True
                elif periodic_diag_last_ts == 0:
                    periodic_diag_last_ts = now_raw
                elif (now_raw - periodic_diag_last_ts) >= PERIODIC_DIAG_S:
                    should_send_periodic = True

            if should_send_periodic:
                diag_set_stage("MQTT_PUBLISH_DIAG_PERIODIC")
                diag_payload = {
                    "device": uid,
                    "ts": now,
                    "fw": FIRMWARE_VERSION,
                    "mem_free": gc.mem_free(),
                    "wifi": sta.isconnected(),
                    "wifi_fail_count": wifi_fail_count,
                    "rssi": sta.status("rssi") if sta.isconnected() else None,
                    "uptime_ms": time.ticks_ms(),
                    "net_op": net_fail.get("op") if net_fail else None,
                    "net_errno": net_fail.get("errno") if net_fail else None,
                    "kind": "periodic",
                    "reset_cause_last": reset_cause
                }

                ok_any = publish_to_all(mqtt_clients, ota_slot, config, sensor_manager, uid, diag_payload, "diag", wdt=wdt)

                periodic_diag_last_ts = now_raw
                if ok_any and net_fail:
                    try:
                        pdiag.clear_net_fail()
                    except Exception:
                        pass
                diag_set_stage("IDLE")

            # --- OTA MQTT keepalive / probe ---
            if ota_slot and not ota_slot.get("client"):
                # probaj povremeno da se poveže (da OTA radi i kad nema publish-a)
                if sta.isconnected() and now_raw >= ota_next_probe_ts:
                    ota_next_probe_ts = now_raw + OTA_PROBE_S
                    if now_raw >= ota_slot["next_retry_ts"]:
                        print("[OTA] probe connect...")
                        diag_set_stage("OTA_PROBE_CONNECT")
                        wdt_feed(wdt)
                        c = mqtt_client.connect_mqtt({"mqtt": ota_slot["cfg"]})
                        wdt_feed(wdt)
                        diag_set_stage("IDLE")
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
                    diag_set_stage("OTA_CHECK_MSG")
                    wdt_feed(wdt)
                    ota_slot["client"].check_msg()
                    wdt_feed(wdt)
                    diag_set_stage("IDLE")
                except:
                    # ako pukne, pusti da publish/probe ponovo uspostavi
                    diag_set_stage("OTA_CHECK_MSG_FAIL")
                    try:
                        ota_slot["client"].disconnect()
                    except:
                        pass
                    ota_slot["client"] = None
                    ota_slot["subscribed"] = False
                    diag_set_stage("IDLE")

            # OTA cycle
            maintenance = ota_slot["maintenance"] if ota_slot else None
            handle_ota_cycle(maintenance, config)
            
            # ---------------------------
            # Sensor.Community independent timer
            # ---------------------------
            if sc_uploader and sta.isconnected():
                if (time.time() - boot_ts) >= BOOT_QUIET_S:

                    if last_sc_ts == 0:
                        last_sc_ts = now_raw

                    # When window elapsed: freeze averaged payload for sending (once per window)
                    if sc_pending_avg is None and sc_cnt and (now_raw - last_sc_ts) >= sc_interval_s:
                        sc_pending_avg = sc_build_avg(sc_sum, sc_cnt)
                        sc_reset(sc_sum, sc_cnt)  # start collecting next window immediately
                        sc_next_retry_ts = now_raw  # allow immediate first try
                        sc_pending_tries = 0

                    # Try send only if we have pending payload AND retry timer elapsed
                    if sc_pending_avg is not None and now_raw >= sc_next_retry_ts:
                        ok = False
                        time.sleep_ms(500)
                        wdt_feed(wdt)

                        if wait_for_network_ready(timeout_seconds=2, wdt=wdt):
                            try:
                                sc_timeout = int(sc_cfg.get("sc_socket_timeout_s", 5) or 5)
                                socket.setdefaulttimeout(sc_timeout)
                            except Exception:
                                pass

                            diag_set_stage("SC_SEND_ENTER")
                            try:
                                wdt_feed(wdt)
                                diag_set_stage("SC_BEFORE_SEND")
                                ok = sc_uploader.send(sc_pending_avg)
                                if ok:
                                    diag_set_stage("SC_AFTER_SEND_OK")
                                else:
                                    diag_set_stage("SC_AFTER_SEND_FALSE")
                            except Exception as exc:
                                ok = False
                                diag_set_stage("SC_SEND_FAIL")
                                print("[SC] send failed:", exc)
                            finally:
                                wdt_feed(wdt)
                                diag_set_stage("IDLE")

                        if ok:
                            print("[SC] sent")
                            sc_pending_avg = None

                            # consume the schedule slot on success
                            last_sc_ts += sc_interval_s
                            while (now_raw - last_sc_ts) >= sc_interval_s:
                                last_sc_ts += sc_interval_s
                        else:
                            sc_pending_tries += 1
                            if sc_pending_tries > sc_retry_max:
                                print("[SC] failed (drop pending, move on)")
                                sc_pending_avg = None
                                sc_pending_tries = 0

                                # consume the slot even on drop (start a fresh window cadence)
                                last_sc_ts += sc_interval_s
                                while (now_raw - last_sc_ts) >= sc_interval_s:
                                    last_sc_ts += sc_interval_s
                            else:
                                print("[SC] failed (will retry)")
                                sc_next_retry_ts = now_raw + sc_retry_interval_s
            elif sc_uploader:
                pass

            # ---------------------------
            # Minute sampling
            # ---------------------------
            if now - last_minute_ts >= 60:
                wdt_feed(wdt)
                diag_set_stage("MEASURE_MINUTE")
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
            
                diag_set_stage("IDLE")

                if pm25 is None and pm10 is None:
                    sensor_fail_streak += 1
                    print("[Sensor] fail streak:", sensor_fail_streak)
                else:
                    sensor_fail_streak = 0

                if sensor_fail_streak >= SENSOR_FAIL_THRESHOLD:
                    print("[Sensor] reinitializing sensors after failures")
                    try:
                        sensors = create_sensors(device_meta)
                        sensor_manager = SensorManager(sensors)
                    except Exception as exc:
                        print("[Sensor] reinit failed:", exc)
                    sensor_fail_streak = 0            
                    wdt_feed(wdt)
            
                # čuvamo poslednje merenje za SC snapshot (normalized)
                if payload and isinstance(payload, dict) and "data" in payload:
                    last_measurement_for_sc = payload["data"]
                
                if sc_uploader and last_measurement_for_sc:
                    sc_accumulate(sc_sum, sc_cnt, last_measurement_for_sc)

                time.sleep_ms(500)

                # Publish minute to all MQTT brokers
                wdt_feed(wdt)

                if (time.time() - boot_ts) < BOOT_QUIET_S:
                    # skip SC/MQTT early after boot to reduce power spikes
                    pass
                else:
                    if not in_safe_mode:
                        diag_set_stage("MQTT_PUBLISH_MINUTE")
                        publish_to_all(mqtt_clients, ota_slot, config, sensor_manager, uid, payload, "minute", wdt=wdt)
                    else:
                        pass

                print("Sent minute (timer:", now - last_minute_ts, ") PM2.5", pm25, "PM10", pm10)

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
                        diag_set_stage("MQTT_PUBLISH_AGG")
                        publish_to_all(mqtt_clients, ota_slot, config, sensor_manager, uid, agg_payload, "agg", wdt=wdt)

                    minute_measurements = []

                last_minute_ts = now

            # ---------------------------
            # FLUSH BUFFERS every 30 sec
            # ---------------------------
            if now - last_flush_ts >= 30:
                if (not in_safe_mode):
                    diag_set_stage("FLUSH_BUFFERS_BEGIN")
                    wdt_feed(wdt)
                    flush_all_buffers(mqtt_clients, config)
                    wdt_feed(wdt)
                    last_flush_ts = now
                    diag_set_stage("FLUSH_BUFFERS_DONE")
                    diag_set_stage("IDLE")

            # Restore LED if needed
            if led_status.is_off():
                led_status.restore()

            diag_set_stage("IDLE")
            time.sleep_ms(300)
        except Exception as exc:
            # Record crash to RTC (best-effort). Do not let diagnostics crash the device.
            try:
                stage = diag_get_stage()
                wifi_ok = bool(sta.isconnected()) if 'sta' in locals() else None
                rssi = sta.status("rssi") if wifi_ok else None
                ts_now = time.time() + UNIX_EPOCH_OFFSET
                pdiag.record_crash(exc, stage=stage, ts=ts_now, wifi=wifi_ok, rssi=rssi)
                _last, _count = pdiag.peek_crash()
            except Exception:
                _count = 1

            # After 3 repeats of the same crash, hard reset the device.
            if _count >= 3:
                machine.reset()

            # Avoid tight exception loops
            time.sleep_ms(300)


# ---------------------------------------------------------------------------
# AUTOSTART
# ---------------------------------------------------------------------------

try:
    main()
except Exception as exc:
    print("FATAL ERROR:", exc)
    # Persist fatal init/runtime exception so it can be reported after reboot.
    try:
        stage = diag_get_stage()
        ts_now = time.time() + UNIX_EPOCH_OFFSET
        pdiag.record_crash(exc, stage=stage, ts=ts_now, wifi=None, rssi=None)
    except Exception:
        pass
    machine.reset()
