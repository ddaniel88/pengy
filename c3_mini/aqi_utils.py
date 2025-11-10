# aqi_utils.py

# pragovi su uprošćeni EU / SC stil
PM25_THRESH = [10, 20, 25, 50, 75]     # => 6 nivoa
PM10_THRESH = [20, 35, 50, 100, 150]   # => 6 nivoa

def _value_to_level(value, thresholds):
    """
    thresholds je rastuća lista.
    vraća index u rasponu 0..len(thresholds)
    """
    if value is None:
        return None
    for idx, limit in enumerate(thresholds):
        if value <= limit:
            return idx
    return len(thresholds)  # preko poslednjeg => najgore

def get_aqi_level(pm25=None, pm10=None):
    """
    Vrati AQI nivo 0..5 (0 = najbolje, 5 = najgore)
    Uzimamo goru vrednost iz PM2.5 i PM10.
    Ako imamo samo jednu, uzimamo nju.
    Ako nemamo nijednu, vraćamo None.
    """
    lvl25 = _value_to_level(pm25, PM25_THRESH) if pm25 is not None else None
    lvl10 = _value_to_level(pm10, PM10_THRESH) if pm10 is not None else None

    if lvl25 is None and lvl10 is None:
        return None
    if lvl25 is None:
        return lvl10
    if lvl10 is None:
        return lvl25
    return max(lvl25, lvl10)
