# net/mqtt_client.py

import machine
import ubinascii
import gc
import time
import socket
from umqtt.simple import MQTTClient

import offline_buffer

try:
    import pengy_diag as pdiag
except Exception:
    pdiag = None

def _stage(name):
    # Best-effort stage marker for MQTT crash forensics.
    if not pdiag:
        return
    try:
        pdiag.set_stage(name)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Helpers

def _is_oserror(exc):
    return isinstance(exc, OSError)

def _errno(exc):
    try:
        if hasattr(exc, "args") and exc.args:
            return exc.args[0]
    except Exception:
        pass
    return None

def _is_network_not_ready(exc):
    # ESP32 often raises OSError(-202) while WiFi says "connected"
    return _is_oserror(exc) and _errno(exc) == -202

def _wifi_kick():
    """
    Non-blocking 'kick' for STA interface (helps after -202 / router restart).
    """
    try:
        import network
        sta = network.WLAN(network.STA_IF)
        if sta.active():
            try:
                sta.disconnect()
            except Exception:
                pass
    except Exception:
        pass


def _dns_warmup(host, port, tries=3, max_backoff_ms=1500):
    """
    Rešava transient OSError(-202) na ESP32:
    - čeka da DNS/routing zaista prorade
    - koristi kratki backoff (ne blokira predugo)
    """
    backoff_ms = 250

    for _ in range(tries):
        try:
            socket.getaddrinfo(host, port)
            return True
        except OSError as exc:
            if _is_network_not_ready(exc):
                _wifi_kick()
            time.sleep_ms(backoff_ms)
            backoff_ms = min(backoff_ms * 2, max_backoff_ms)
        except Exception:
            time.sleep_ms(backoff_ms)
            backoff_ms = min(backoff_ms * 2, max_backoff_ms)

    return False


def _tcp_probe(host, port, timeout_s=2):
    """
    Quick TCP probe before entering umqtt.connect().
    Helps avoid hard hangs inside socket/MQTT connect path.
    """
    s = None
    try:
        addr = socket.getaddrinfo(host, port)[0][-1]
        s = socket.socket()
        try:
            s.settimeout(timeout_s)
        except Exception:
            pass
        s.connect(addr)
        return True
    except OSError as exc:
        if _is_network_not_ready(exc):
            _wifi_kick()
        return False
    except Exception:
        return False
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Connect

def connect_mqtt(config):
    """
    Prima config oblika: {"mqtt": {host, port, username, password, tls, ...}}
    i konektuje jedan MQTT klijent.

    Namerno kratko:
      - mali broj pokušaja
      - mali backoff
      - nema dugačkog čekanja (main loop ostaje živ)
    """

    mqtt_config = config.get("mqtt", {})
    host = mqtt_config.get("host", "test.mosquitto.org")
    port = int(mqtt_config.get("port", 1883))

    username = mqtt_config.get("username", None)
    password = mqtt_config.get("password", None)
    use_tls = bool(mqtt_config.get("tls", False))

    connect_retries = int(mqtt_config.get("connect_retries", 3))
    backoff_ms = int(mqtt_config.get("connect_backoff_ms", 500))
    sock_timeout = int(mqtt_config.get("socket_timeout", 5))

    try:
        socket.setdefaulttimeout(sock_timeout)
    except AttributeError:
        pass

    client_id = b"pengy_" + ubinascii.hexlify(machine.unique_id())

    if isinstance(username, str):
        username = username.encode()
    if isinstance(password, str):
        password = password.encode()

    _stage("MQTT_CONN_CFG")
    print("MQTT connecting →", host, "port:", port, "TLS:", use_tls)

    # DNS warmup (short)
    _stage("MQTT_CONN_DNS_WARMUP_BEGIN")
    _dns_warmup(host, port, tries=3)
    _stage("MQTT_CONN_DNS_WARMUP_DONE")

    _stage("MQTT_CONN_TCP_PROBE_BEGIN")
    if not _tcp_probe(host, port, timeout_s=min(sock_timeout, 2)):
        _stage("MQTT_CONN_TCP_PROBE_FAIL")
        return None
    _stage("MQTT_CONN_TCP_PROBE_OK")

    for attempt in range(1, connect_retries + 1):
        client = MQTTClient(
            client_id=client_id,
            server=host,
            port=port,
            user=username,
            password=password,
            ssl=use_tls,
            ssl_params={"server_hostname": host} if use_tls else {},
        )

        try:
            _stage("MQTT_CONN_CONNECT_BEGIN_A{}".format(attempt))
            client.connect()
            _stage("MQTT_CONN_CONNECT_OK_A{}".format(attempt))
            print("MQTT connected.")
            return client

        except Exception as exc:
            _stage("MQTT_CONN_CONNECT_FAIL_A{}".format(attempt))
            print("MQTT connect failed (attempt", attempt, "):", type(exc), getattr(exc, "args", exc))
            if _is_network_not_ready(exc):
                _wifi_kick()

            try:
                if hasattr(client, "sock") and client.sock:
                    try:
                        client.sock.close()
                    except Exception:
                        pass
                    client.sock = None
            except Exception:
                pass

            try:
                client.disconnect()
            except Exception:
                pass

            gc.collect()
            time.sleep_ms(backoff_ms)
            
    _stage("MQTT_CONN_GIVE_UP")
    return None


def _is_network_error(exc):
    # treat all OSError as network error in practice
    return isinstance(exc, OSError)


# ---------------------------------------------------------------------------
# Publish (connect on demand)

def publish(
    client,
    config,
    topic,
    message,
    retain=False,
    qos=0,
    message_type="minute",
    from_flush=False,
):
    """
    Returns (client, ok, was_network_error, did_reconnect).

    - connect on demand (if client is None)
    - on OSError -> drop client + let caller retry later
    - buffer only non-minute when it was a network error
    """

    last_network_error = False
    did_reconnect = False

    # keep this short; caller (main loop) controls cadence/cooldown
    for _ in range(2):
        _stage("MQTT_PUB_ENTER_" + str(message_type))

        if not client:
            _stage("MQTT_PUB_NEED_CONNECT_" + str(message_type))
            client = connect_mqtt(config)
            did_reconnect = bool(client)

        if not client:
            last_network_error = True
        else:
            try:
                _stage("MQTT_BEFORE_GC")
                gc.collect()
                _stage("MQTT_AFTER_GC")
                
                try:
                    # Force per-socket timeout (defensive; prevents indefinite block on write/read)
                    mqtt_cfg = (config or {}).get("mqtt", {})
                    sock_timeout = int(mqtt_cfg.get("socket_timeout", 5) or 5)

                    try:
                        socket.setdefaulttimeout(sock_timeout)
                    except Exception:
                        pass

                    # umqtt.simple keeps socket on client.sock
                    if hasattr(client, "sock") and client.sock:
                        try:
                            client.sock.settimeout(sock_timeout)
                        except Exception:
                            pass
                except Exception:
                    pass
                
                _stage("MQTT_PUB_CALL_BEGIN_" + str(message_type))
                client.publish(topic, message, retain=retain, qos=qos)
                _stage("MQTT_PUB_CALL_OK_" + str(message_type))
                return client, True, False, did_reconnect

            except Exception as exc:
                _stage("MQTT_PUB_CALL_EXC_" + str(message_type))
                print("MQTT publish failed:", exc)
                
                if pdiag and isinstance(exc, OSError):
                    try:
                        pdiag.set_net_fail("mqtt_publish:" + str(message_type), _errno(exc))
                    except Exception:
                        pass

                if _is_network_error(exc):
                    last_network_error = True
                    if _is_network_not_ready(exc):
                        _wifi_kick()
                    try:
                        if hasattr(client, "sock") and client.sock:
                            try:
                                client.sock.close()
                            except Exception:
                                pass
                            client.sock = None
                    except Exception:
                        pass

                    try:
                        client.disconnect()
                    except Exception:
                        pass

                    client = None
                else:
                    return client, False, False, did_reconnect

            finally:
                gc.collect()

    if (not from_flush) and last_network_error and message_type != "minute":
        try:
            offline_buffer.add_message(message_type, message)
        except Exception as exc:
            print("Failed to buffer MQTT message:", exc)

    return client, False, last_network_error, did_reconnect


# ---------------------------------------------------------------------------
# Downlink / subscribe

def setup_downlink(client, topic: str, callback):
    """
    Subscribe + callback (call again after reconnect).
    """
    if not client:
        return False

    try:
        client.set_callback(callback)
        client.subscribe(topic)
        print("Subscribed for commands on", topic)
        return True
    except Exception as exc:
        print("Downlink subscribe error:", exc)
        return False
