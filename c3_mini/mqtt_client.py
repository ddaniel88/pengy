# mqtt_client.py
import machine
import ubinascii
from umqtt.simple import MQTTClient

def connect_mqtt(config):
    mqtt_config = config.get("mqtt", {})
    host = mqtt_config.get("host", "test.mosquitto.org")
    port = mqtt_config.get("port", 1883)

    client_id = b"sen55_" + ubinascii.hexlify(machine.unique_id())

    client = MQTTClient(
        client_id=client_id,
        server=host,
        port=port,
        user=mqtt_config.get("user", None),
        password=mqtt_config.get("password", None),
        keepalive=60,
    )

    try:
        client.connect()
        print("MQTT connected to", host, port)
        return client
    except Exception as exc:
        print("MQTT connect failed:", exc)
        return None


def publish_safe(client, config, topic: bytes, message: str, retain: bool = False, qos: int = 0):
    if not client:
        return client

    try:
        client.publish(topic, message, retain=retain, qos=qos)
        return client
    except Exception as exc:
        print("MQTT publish error:", exc)
        # reconnect
        new_client = connect_mqtt(config)
        if new_client:
            try:
                new_client.publish(topic, message, retain=retain, qos=qos)
            except Exception as exc2:
                print("MQTT retry failed:", exc2)
        return new_client


def setup_downlink(client, topic: str, callback):
    if not client:
        return
    client.set_callback(callback)
    client.subscribe(topic)
    print("Subscribed for commands on", topic)
