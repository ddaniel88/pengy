# setup.py
import network
import socket
import time
import machine
import ujson
import os

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "wifi": {"ssid": "", "password": ""},
    "mqtt": {
        "host": "test.mosquitto.org",
        "port": 1883,
        "base_topic": "pengy/rs/nis",
        "retain": True,
    },
    "device": {
        "uid": "pengy-wifi-0004e3ec",
        "name": "Peskare",
        "description": "",
        "lat": 43.32451475,
        "lon": 21.91667465,
        "altitude": 210,
        "ground_offset": 1.5,
        "status": "active",
        "streams": ["pm1", "pm4", "pm2_5", "pm10", "temperature", "humidity"],
    },
    "sampling": {
        "samples_per_min": 5,
        "agg_window": 10,
        "trim": True,
    },
    "external": {
        "sensor_community": {
            "enabled": True,
            "base_url": "https://api.sensor.community/v1/push-sensor-data/",
            "sensor_id": "esp32-0004e3ec",
            "interval_seconds": 145,
            "software_version": "pengy-wifi-1.0"
        },
        "pengy_api": {
            "enabled": False,
            "base_url": "",
            "api_key": ""
        }
    },
    "ota": {
        "channel": "latest",
        "latest_manifest_url": "https://raw.githubusercontent.com/ddaniel88/pengy/refs/heads/C3mini-SEN55/ota/latest/manifest.json",
        "beta_manifest_url": "https://raw.githubusercontent.com/ddaniel88/pengy/refs/heads/C3mini-SEN55/ota/beta/manifest.json",
        # opciono, za ručni override
        "manifest_url": ""
    },
    "security": {
        "admin_pin": "1234"
    }
}

# ---------- helpers ----------

def load_config_or_default():
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = ujson.loads(f.read())
    except Exception:
        cfg = DEFAULT_CONFIG
    
    # security
    if "security" not in cfg:
        cfg["security"] = {"admin_pin": "1234"}
    if "admin_pin" not in cfg["security"]:
        cfg["security"]["admin_pin"] = "1234"

    # ota
    if "ota" not in cfg:
        cfg["ota"] = DEFAULT_CONFIG["ota"].copy()

    ota_cfg = cfg["ota"]
    if "channel" not in ota_cfg:
        ota_cfg["channel"] = "latest"
    if "latest_manifest_url" not in ota_cfg:
        ota_cfg["latest_manifest_url"] = DEFAULT_CONFIG["ota"]["latest_manifest_url"]
    if "beta_manifest_url" not in ota_cfg:
        ota_cfg["beta_manifest_url"] = DEFAULT_CONFIG["ota"]["beta_manifest_url"]
    if "manifest_url" not in ota_cfg:
        ota_cfg["manifest_url"] = ""

    cfg["ota"] = ota_cfg

    return cfg

def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        f.write(ujson.dumps(cfg))

def start_access_point():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="SEN55-setup", password="12345678")
    print("AP started: SEN55-setup / 12345678")
    return ap

# ---------- HTML ----------
# mala wifi forma
WIFI_FORM_HTML = """HTTP/1.0 200 OK
Content-Type: text/html

<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>WiFi setup</title>
<style>
body{font-family:sans-serif;background:#f2f4f8;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.card{background:#fff;padding:1.2rem 1.4rem;border-radius:.7rem;box-shadow:0 10px 25px rgba(0,0,0,.08);width:320px}
label{display:block;font-size:.7rem;margin-bottom:.25rem}
input{width:100%;padding:.45rem .5rem;margin-bottom:.5rem;border:1px solid #d0d7de;border-radius:.35rem}
button{width:100%;padding:.5rem;background:#0066cc;border:none;color:#fff;border-radius:.35rem}
h1{font-size:1rem;margin:0 0 .5rem 0}
p{font-size:.65rem;color:#555;margin-bottom:.75rem}
</style>
</head><body>
<div class="card">
<h1>WiFi setup</h1>
<p>Unesi SSID i lozinku.</p>
<form method="POST" action="/wifi">
<label>SSID</label>
<input name="wifi_ssid" />
<label>Lozinka</label>
<input type="password" name="wifi_pass" />
<button type="submit">Sačuvaj</button>
</form>
</div>
</body></html>
"""

def _html_escape(s):
    s = str(s)
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    s = s.replace('"', "&quot;")
    return s

# puna admin strana (mala ali sa svim poljima)
def admin_html(cfg):
    try:
        with open("admin_setup.html", "r") as f:
            html = f.read()
    except Exception as e:
        print("⚠️  admin_setup.html not found:", e)
        # preusmeri korisnika na WiFi setup stranu
        return "HTTP/1.0 302 Found\r\nLocation: /wifi\r\n\r\n"

    wifi = cfg.get("wifi", {})
    mqtt = cfg.get("mqtt", {})
    device = cfg.get("device", {})
    sampling = cfg.get("sampling", {})
    external = cfg.get("external", {})
    sc_cfg = external.get("sensor_community", {})
    pengy_cfg = external.get("pengy_api", {})

    # tekstualna polja
    replacements = {
        "{{wifi_ssid}}": wifi.get("ssid", ""),
        "{{mqtt_host}}": mqtt.get("host", ""),
        "{{mqtt_port}}": mqtt.get("port", ""),
        "{{mqtt_base_topic}}": mqtt.get("base_topic", ""),
        "{{device_name}}": device.get("name", ""),
        "{{description}}": device.get("description", ""),
        "{{lat}}": device.get("lat", ""),
        "{{lon}}": device.get("lon", ""),
        "{{alt}}": device.get("altitude", ""),
        "{{ground_offset}}": device.get("ground_offset", ""),
        "{{samples_per_min}}": sampling.get("samples_per_min", ""),
        "{{agg_window}}": sampling.get("agg_window", ""),
        "{{sc_sensor_id}}": sc_cfg.get("sensor_id", ""),
        "{{sc_interval}}": sc_cfg.get("interval_seconds", ""),
        "{{pengy_base_url}}": pengy_cfg.get("base_url", ""),
        "{{pengy_api_key}}": pengy_cfg.get("api_key", ""),
    }

    for token, value in replacements.items():
        html = html.replace(token, _html_escape(value))

    # checkbox-ovi
    html = html.replace(
        "{{mqtt_retain_checked}}",
        "checked" if mqtt.get("retain") else ""
    )
    html = html.replace(
        "{{trim_checked}}",
        "checked" if sampling.get("trim") else ""
    )
    html = html.replace(
        "{{sc_enabled_checked}}",
        "checked" if sc_cfg.get("enabled") else ""
    )
    html = html.replace(
        "{{pengy_enabled_checked}}",
        "checked" if pengy_cfg.get("enabled") else ""
    )

    return "HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n" + html

PIN_FORM_HTML = """HTTP/1.0 200 OK
Content-Type: text/html

<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>PIN</title>
<style>
body{font-family:sans-serif;background:#f2f4f8;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}
.card{background:#fff;padding:1rem 1.2rem;border-radius:.5rem;box-shadow:0 10px 25px rgba(0,0,0,.08);width:280px}
label{display:block;margin-bottom:.3rem;font-size:.7rem}
input{width:100%;padding:.4rem .45rem;margin-bottom:.4rem;border:1px solid #d0d7de;border-radius:.35rem}
button{width:100%;padding:.45rem;background:#0066cc;border:none;color:#fff;border-radius:.35rem}
h1{font-size:.9rem;margin-bottom:.4rem}
</style>
</head><body>
<div class="card">
<h1>Admin PIN</h1>
<form method="POST" action="/admin">
<label>PIN</label>
<input name="admin_pin" />
<button type="submit">Uđi</button>
</form>
</div>
</body></html>
"""

# ---------- server ----------

def parse_request(req_text):
    # vrati (method, path, body)
    lines = req_text.split("\r\n")
    first = lines[0]
    parts = first.split(" ")
    method = parts[0]
    path = parts[1]
    body = ""
    if "\r\n\r\n" in req_text:
        body = req_text.split("\r\n\r\n", 1)[1]
    return method, path, body

def parse_form(body: str):
    data = {}
    for pair in body.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            data[k] = v.replace("+", " ")
    return data

def run_setup_mode(timeout_seconds=180):
    # podigni AP
    ap = start_access_point()

    # pripremi server
    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    s.bind(addr)
    s.listen(1)
    s.settimeout(1)  # da možemo da proveravamo timeout
    print("Setup HTTP server on 0.0.0.0:80, timeout", timeout_seconds, "s")

    cfg = load_config_or_default()
    admin_pin = cfg["security"]["admin_pin"]

    start_ms = time.ticks_ms()

    while True:
        # timeout check
        if time.ticks_diff(time.ticks_ms(), start_ms) > timeout_seconds * 1000:
            print("Setup timeout -> exiting setup mode")
            break

        try:
            client, remote = s.accept()
        except OSError:
            # ništa nije došlo u ovom 1s
            continue

        start_ms = time.ticks_ms()
        
        req = client.recv(2048)
        try:
            req_text = req.decode()
        except:
            req_text = req.decode("utf-8", "ignore")

        method, path, body = parse_request(req_text)
        print("HTTP", method, path, "from", remote)

        # ----- /wifi -----
        if path.startswith("/wifi"):
            if method == "POST":
                form = parse_form(body)
                ssid = form.get("wifi_ssid", "")
                pwd = form.get("wifi_pass", "")
                cfg["wifi"]["ssid"] = ssid
                cfg["wifi"]["password"] = pwd
                save_config(cfg)
                client.send(b"HTTP/1.0 200 OK\r\n\r\nSaved. Rebooting...")
                client.close()
                time.sleep(1)
                machine.reset()
                return
            else:
                client.send(WIFI_FORM_HTML.encode())
                client.close()
                continue

        # ----- /admin -----
        elif path.startswith("/admin"):
            # ako je POST sa admin_pin samo -> verifikuj pa prikaži formu
            if method == "POST":
                form = parse_form(body)

                # ako je samo PIN, a ne full form
                if "admin_pin" in form and "wifi_ssid" not in form:
                    if form["admin_pin"] == admin_pin:
                        # prikaži full form
                        client.send(admin_html(cfg).encode())
                    else:
                        client.send(PIN_FORM_HTML.encode())
                    client.close()
                    continue

                # ovde smo ako je POST sa full formom
                if form.get("admin_pin", admin_pin) != admin_pin:
                    # pogrešan pin
                    client.send(PIN_FORM_HTML.encode())
                    client.close()
                    continue

                # WiFi
                cfg["wifi"]["ssid"] = form.get("wifi_ssid", cfg["wifi"]["ssid"])
                cfg["wifi"]["password"] = form.get("wifi_pass", cfg["wifi"]["password"])

                # MQTT
                cfg["mqtt"]["host"] = form.get("mqtt_host", cfg["mqtt"]["host"])
                try:
                    cfg["mqtt"]["port"] = int(form.get("mqtt_port", cfg["mqtt"]["port"]))
                except:
                    pass
                cfg["mqtt"]["base_topic"] = form.get("mqtt_base_topic", cfg["mqtt"]["base_topic"])
                cfg["mqtt"]["retain"] = ("mqtt_retain" in form)

                # Device
                cfg["device"]["name"] = form.get("device_name", cfg["device"].get("name", ""))
                try:
                    cfg["device"]["lat"] = float(form.get("lat", cfg["device"].get("lat", 0)))
                    cfg["device"]["lon"] = float(form.get("lon", cfg["device"].get("lon", 0)))
                    cfg["device"]["altitude"] = float(form.get("alt", cfg["device"].get("altitude", 0)))
                    cfg["device"]["description"] = form.get("description", cfg["device"].get("description", ""))
                    cfg["device"]["ground_offset"] = float(form.get("ground_offset", cfg["device"].get("ground_offset", 0)))
                except:
                    pass

                # Sampling
                try:
                    cfg["sampling"]["samples_per_min"] = int(form.get("samples_per_min", cfg["sampling"]["samples_per_min"]))
                    cfg["sampling"]["agg_window"] = int(form.get("agg_window", cfg["sampling"]["agg_window"]))
                except:
                    pass
                cfg["sampling"]["trim"] = ("trim" in form)

                # sensor.community
                sc_cfg = cfg["external"].get("sensor_community", {})
                sc_cfg["enabled"] = ("sc_enabled" in form)
                sc_cfg["sensor_id"] = form.get("sc_sensor_id", sc_cfg.get("sensor_id", ""))
                try:
                    sc_cfg["interval_seconds"] = int(form.get("sc_interval", sc_cfg.get("interval_seconds", 145)))
                except:
                    pass
                cfg["external"]["sensor_community"] = sc_cfg

                # Pengy API
                pengy_cfg = cfg["external"].get("pengy_api", {})
                pengy_cfg["enabled"] = ("pengy_enabled" in form)
                pengy_cfg["base_url"] = form.get("pengy_base_url", pengy_cfg.get("base_url", ""))
                pengy_cfg["api_key"] = form.get("pengy_api_key", pengy_cfg.get("api_key", ""))
                cfg["external"]["pengy_api"] = pengy_cfg
                
                # OTA
                ota_cfg = cfg.get("ota", {})
                ota_cfg["channel"] = form.get("ota_channel", ota_cfg.get("channel", "latest"))
                ota_cfg["latest_manifest_url"] = form.get(
                    "ota_latest_manifest_url",
                    ota_cfg.get("latest_manifest_url", "")
                )
                ota_cfg["beta_manifest_url"] = form.get(
                    "ota_beta_manifest_url",
                    ota_cfg.get("beta_manifest_url", "")
                )
                # manifest_url
                cfg["ota"] = ota_cfg

                save_config(cfg)
                client.send(b"HTTP/1.0 200 OK\r\n\r\nSaved. Rebooting...")
                client.close()
                time.sleep(1)
                machine.reset()
                return

            else:
                # GET /admin -> prvo traži PIN
                client.send(PIN_FORM_HTML.encode())
                client.close()
                continue

        else:
            # sve ostalo -> pošalji osnovnu wifi stranu
            client.send(WIFI_FORM_HTML.encode())
            client.close()

    # timeout se desio -> ugasi AP i vrati se
    try:
        ap.active(False)
    except Exception:
        pass
    s.close()
    print("Setup finished (timeout)")
