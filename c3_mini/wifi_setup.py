# wifi_setup.py
import network
import socket
import time
import machine
import ujson

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "wifi": {"ssid": "", "password": ""},
    "mqtt": {
        "host": "test.mosquitto.org",
        "port": 1883,
        "base_topic": "pengy/rs",
        "retain": True
    },
    "sampling": {
        "samples_per_min": 5,
        "trim": True,
        "agg_window": 10
    }
}

def save_config(config: dict):
    with open(CONFIG_FILE, "w") as file:
        file.write(ujson.dumps(config))

def start_access_point():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="SEN55-setup", password="12345678")
    print("AP started, connect to SEN55-setup / 12345678")
    return ap

def get_setup_form_html() -> str:
    try:
        with open("wifi_login.html", "r") as f:
            return "HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n" + f.read()
    except:
        return """HTTP/1.0 200 OK

<html><body>
<h3>SEN55 WiFi setup</h3>
<form method="POST">
SSID: <input name="ssid" /><br/>
PASS: <input name="password" type="password" /><br/>
<button type="submit">Save</button>
</form>
</body></html>
"""

def run_setup_server():
    address = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    server_socket = socket.socket()
    server_socket.bind(address)
    server_socket.listen(1)
    print("Setup HTTP server on", address)

    html_form = get_setup_form_html()

    while True:
        client_socket, remote_addr = server_socket.accept()
        request_raw = client_socket.recv(1024)
        try:
            request_text = request_raw.decode()
        except UnicodeError:
            request_text = request_raw.decode("utf-8", "ignore")

        if "POST" in request_text:
            body = request_text.split("\r\n\r\n", 1)[1]
            form_data = {}
            for pair in body.split("&"):
                if "=" in pair:
                    key, val = pair.split("=", 1)
                    form_data[key] = val.replace("+", " ")

            ssid = form_data.get("ssid", "")
            password = form_data.get("password", "")
            print("Got SSID/PASS:", ssid, password)

            config = DEFAULT_CONFIG
            config["wifi"]["ssid"] = ssid
            config["wifi"]["password"] = password
            save_config(config)

            client_socket.send(b"HTTP/1.0 200 OK\r\n\r\nSaved. Rebooting...")
            client_socket.close()
            time.sleep(2)
            machine.reset()
            return
        else:
            client_socket.send(html_form.encode())
            client_socket.close()

def run_setup_mode():
    start_access_point()
    run_setup_server()
