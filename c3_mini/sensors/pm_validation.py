"""PM publication policy: reject the whole PM group outside 0..1000 ug/m3."""

PM1_KEYS = ("pm1", "pm1_0", "pm01")
PM25_KEYS = ("pm25", "pm2_5", "pm_2_5")
PM4_KEYS = ("pm4", "pm04", "pm4_0", "pm_4", "pm_4_0", "pm4.0")
PM10_KEYS = ("pm10", "pm10_0", "pm_10")
PM_KEYS = PM1_KEYS + PM25_KEYS + PM4_KEYS + PM10_KEYS
PM_MAX = 1000


def first_value(data, keys):
    """Pick the first present value, including a valid zero."""
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def invalid_pm(data):
    for key in PM_KEYS:
        value = data.get(key)
        if value is None:
            continue
        # The comparison also rejects NaN and both infinities.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return True
        if not 0 <= value <= PM_MAX:
            return True
    return False


def sanitize_pm(data):
    """Omit all PM fields if any is invalid; preserve meteo and the input."""
    if invalid_pm(data):
        return {key: value for key, value in data.items() if key not in PM_KEYS}
    return data


def sanitize_pm_message(message):
    """Validate encoded minute/aggregate messages, including old buffer entries."""
    import ujson

    try:
        payload = ujson.loads(message)
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        clean = sanitize_pm(data)
        if not clean:
            return None
        if clean is data:
            return message
        payload["data"] = clean
        return ujson.dumps(payload)
    except (ValueError, TypeError, AttributeError):
        return None
