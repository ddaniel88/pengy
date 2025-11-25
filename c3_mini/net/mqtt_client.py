# net/mqtt_client.py
import machine
import ubinascii
import gc
from umqtt.simple import MQTTClient

import offline_buffer  # naš novi modul


def connect_mqtt(config):
    mqtt_config = config.get("mqtt", {})
    host = mqtt_config.get("host", "test.mosquitto.org")
    port = mqtt_config.get("port", 1883)

    client_id = b"pengy_" + ubinascii.hexlify(machine.unique_id())

    client = MQTTClient(
        client_id=client_id,
        server=host,
        port=port,
    )
    try:
        client.connect()
        print("MQTT connected")
        return client
    except Exception as exc:
        print("MQTT connect failed:", exc)
        gc.collect()
        return None


def _is_network_error(exc):
    # na ESP/MicroPython-u mrežne greške uglavnom budu OSError
    # možemo ovo kasnije proširiti po kodovima
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
    Vrati (client, ok, was_network_error)
    - ok = True ako je konačno poslato
    - was_network_error = True ako je razlog pada mreža (bitno za main da zna šta da radi s RAM porukama)
    """
    last_network_error = False

    for _ in range(3):  # 3 pokušaja
        if not client:
            client = connect_mqtt(config)

        if not client:
            # nema ni konekcije – sigurno je mreža
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
                    # pokušaćemo opet
                    try:
                        client.disconnect()
                    except:
                        pass
                    client = None  # forsiraj reconnect
                else:
                    # nije mreža – nema buffera, nema dalje
                    return client, False, False
            finally:
                gc.collect()

    # ako smo došli ovde – nije uspelo ni posle 3 pokušaja
    # ako je mreža i poruka NIJE minutna i nije iz flush-a – upiši u fajl
    if (not from_flush) and last_network_error and message_type != "minute":
        try:
            offline_buffer.add_message(message_type, message)
        except Exception as exc:
            print("Failed to buffer MQTT message:", exc)

    return client, False, last_network_error


def setup_downlink(client, topic: str, callback):
    if not client:
        return
    client.set_callback(callback)
    client.subscribe(topic)
    print("Subscribed for commands on", topic)

