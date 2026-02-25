# offline_buffer.py
import ujson
import os

BUFFER_FILE = "offline_buffer.jsonl"
MAX_BYTES = 60_000  # oko 60KB, dovoljno za više dana agregata


def _trim_if_needed():
    try:
        st = os.stat(BUFFER_FILE)
    except OSError:
        return

    size = st[6]
    if size <= MAX_BYTES:
        return

    # ako je preveliko – ostavi samo poslednjih ~200 linija
    try:
        with open(BUFFER_FILE, "r") as f:
            lines = f.readlines()
        lines = lines[-200:]
        with open(BUFFER_FILE, "w") as f:
            f.writelines(lines)
    except Exception as exc:
        print("offline_buffer trim error:", exc)


def add_message(msg_type: str, payload: str):
    """
    Snimamo SAMO ako je mrežni problem i SAMO agregatne (mqtt_client će nas tako zvati).
    topic ne čuvamo – izračunaćemo ga iz config-a kod flush-a.
    """
    entry = {
        "type": msg_type,
        "payload": payload,
    }
    try:
        with open(BUFFER_FILE, "a") as f:
            f.write(ujson.dumps(entry))
            f.write("\n")
    except Exception as exc:
        print("offline_buffer write error:", exc)
        return

    _trim_if_needed()


def _load_all():
    items = []
    try:
        with open(BUFFER_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(ujson.loads(line))
                except Exception:
                    # ako je neka linija oštećena – preskoči
                    pass
    except OSError:
        pass
    return items


def _rewrite(items):
    try:
        with open(BUFFER_FILE, "w") as f:
            for item in items:
                f.write(ujson.dumps(item))
                f.write("\n")
    except Exception as exc:
        print("offline_buffer rewrite error:", exc)


import time
try:
    import machine
except Exception:
    machine = None


def flush_buffer(mqtt_client, config, publish_fn):
    """
    WDT-safe flush:
    - max messages per cycle
    - time budget limit
    - small yield between publishes
    """

    MAX_PER_CYCLE = int(config.get("recovery", {}).get("flush_max_per_cycle", 5) or 5)
    TIME_BUDGET_MS = int(config.get("recovery", {}).get("flush_time_budget_ms", 2000) or 2000)

    items = _load_all()
    if not items:
        return mqtt_client, 0

    base_topic = config.get("mqtt", {}).get("base_topic", "pengy/rs/nis")
    uid = config.get("device", {}).get("uid", "unknown")

    remaining = []
    sent_count = 0

    start_ms = time.ticks_ms()

    for item in items:

        # ---- HARD LIMIT 1: time budget ----
        if time.ticks_diff(time.ticks_ms(), start_ms) > TIME_BUDGET_MS:
            remaining.append(item)
            continue

        # ---- HARD LIMIT 2: max per cycle ----
        if sent_count >= MAX_PER_CYCLE:
            remaining.append(item)
            continue

        msg_type = item.get("type", "aggregate")
        payload = item.get("payload", "")

        if msg_type == "aggregate":
            topic = "{}/{}/agg".format(base_topic, uid)
        else:
            topic = "{}/{}/{}".format(base_topic, uid, msg_type)

        mqtt_client, ok, _, _ = publish_fn(
            mqtt_client,
            config,
            topic.encode(),
            payload,
            False,
            0,
            msg_type,
            from_flush=True,
        )

        if ok:
            sent_count += 1
        else:
            remaining.append(item)

        # ---- YIELD to prevent WDT ----
        try:
            time.sleep_ms(20)
        except Exception:
            pass

    # Dodaj sve neobrađene poruke koje nismo stigli
    if sent_count < len(items):
        remaining.extend(items[sent_count:])

    _rewrite(remaining)

    if sent_count:
        print("offline_buffer: sent", sent_count, "message(s)")

    return mqtt_client, sent_count
