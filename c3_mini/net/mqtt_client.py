# net/mqtt_client.py

import machine
import ubinascii
import gc
import ssl
from umqtt.simple import MQTTClient

import offline_buffer


def connect_mqtt(config):
    """
    Prima config oblika: {"mqtt": {host, port, username, password, tls}}
    i konektuje jedan MQTT klijent.
    """

    mqtt_config = config.get("mqtt", {})

    host = mqtt_config.get("host", "test.mosquitto.org")
    port = mqtt_config.get("port", 1883)

    username = mqtt_config.get("username", None)
    password = mqtt_config.get("password", None)
    use_tls = mqtt_config.get("tls", False)

    client_id = b"pengy_" + ubinascii.hexlify(machine.unique_id())

    # string → bytes
    if isinstance(username, str):
        username = username.encode()
    if isinstance(password, str):
        password = password.encode()

    # TLS params sa SNI (HiveMQ requires this)
    ssl_params = {}
    if use_tls:
        try:
            ssl_params = {"server_hostname": host}
        except Exception as exc:
            print("SSL param error:", exc)
            ssl_params = {}

    print("MQTT connecting →", host, "port:", port, "TLS:", use_tls)

    client = MQTTClient(
        client_id=client_id,
        server=host,
        port=port,
        user=username,
        password=password,
        ssl=use_tls,
        ssl_params=ssl_params,
    )

    try:
        client.connect()
        print("MQTT connected.")
        return client

    except Exception as exc:
        print("MQTT connect failed:", type(exc), getattr(exc, "args", exc))
        gc.collect()
        return None


def _is_network_error(exc):
    return isinstance(exc, OSError)


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
    Vrati (client, ok, was_network_error).

    Ako mreža padne → 3 puta će pokušati reconnect.
    Ako nije minute poruka → upisuje u offline fajl (file-based).
    """

    last_network_error = False

    for _ in range(3):
        if not client:
            client = connect_mqtt(config)

        if not client:
            last_network_error = True
        else:
            try:
                gc.collect()
                client.publish(topic, message, retain=retain, qos=qos)
                return client, True, False

            except Exception as exc:
                print("MQTT publish failed:", exc)

                if _is_network_error(exc):
                    last_network_error = True
                    try:
                        client.disconnect()
                    except:
                        pass
                    client = None
                else:
                    return client, False, False

            finally:
                gc.collect()

    # posle 3 pokušaja – nije uspelo
    if (not from_flush) and last_network_error and message_type != "minute":
        try:
            offline_buffer.add_message(message_type, message)
        except Exception as exc:
            print("Failed to buffer MQTT message:", exc)

    return client, False, last_network_error


def setup_downlink(client, topic: str, callback):
    """
    Podešava subscribe i callback za downlink komande (OTA).
    """
    if not client:
        print("Downlink setup skipped (no client).")
        return

    try:
        client.set_callback(callback)
        client.subscribe(topic)
        print("Subscribed for commands on", topic)
    except Exception as exc:
        print("Downlink subscribe error:", exc)


def connect_named(config, section_name):
    """
    Kreira MQTT klijent prema config[section_name].
    Koristi privremeni config: {"mqtt": cfg}.
    """
    mqtt_cfg = config.get(section_name, {})

    if not mqtt_cfg.get("enabled", False):
        print(section_name, "disabled.")
        return None, None

    print("Connecting MQTT section:", section_name)

    temp = {"mqtt": mqtt_cfg}
    client = connect_mqtt(temp)

    if not client:
        print("MQTT connect failed for section:", section_name)

    return client, mqtt_cfg
