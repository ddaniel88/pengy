# pengy_diag.py
# RTC-backed diagnostics for Pengy (best-effort, never crash the device).
#
# Stores:
# - last stage (for context)
# - last crash + repeat count (same crash fingerprint)
#
# Notes:
# - RTC memory is small. Keep payload tiny and always truncate strings.
# - All functions are best-effort; failures are swallowed.

import time
import gc

RTC_ENABLED = True  # Set False to disable RTC usage completely (RAM-only no-op)

# Developer constants (keep small; RTC is limited)
MAX_MSG_LEN = 180
MAX_TB_LEN = 650

# Keys inside RTC JSON store
_KEY_STAGE = "stage"
_KEY_CRASH_LAST = "crash_last"
_KEY_CRASH_COUNT = "crash_count"
_KEY_CRASH_KEY = "crash_key"
_KEY_VER = "v"


def _truncate(s, limit):
    if not s:
        return s
    if len(s) <= limit:
        return s
    return s[:limit]


def _truncate_tail(s, limit):
    if not s:
        return s
    if len(s) <= limit:
        return s
    return s[-limit:]
    

def _get_rtc():
    if not RTC_ENABLED:
        return None
    try:
        import machine
        return machine.RTC()
    except Exception:
        return None


def _rtc_load():
    rtc = _get_rtc()
    if not rtc:
        return {}
    try:
        raw = rtc.memory()
        if not raw:
            return {}
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        import ujson
        return ujson.loads(raw) if raw else {}
    except Exception:
        return {}


def _rtc_save(obj):
    rtc = _get_rtc()
    if not rtc:
        return False

    # Always include a small version marker for future migrations.
    if _KEY_VER not in obj:
        obj[_KEY_VER] = 1

    try:
        import ujson
        rtc.memory(ujson.dumps(obj))
        return True
    except Exception:
        return False


def set_stage(stage: str):
    store = _rtc_load()
    store[_KEY_STAGE] = {"stage": stage, "t_ms": time.ticks_ms()}
    _rtc_save(store)


def get_stage():
    store = _rtc_load()
    return store.get(_KEY_STAGE)


def _format_traceback_tail(exc) -> str:
    try:
        import sys
        import uio
        s = uio.StringIO()
        sys.print_exception(exc, s)
        tb = s.getvalue()
        return _truncate_tail(tb, MAX_TB_LEN)
    except Exception:
        return None


def _make_crash_key(etype: str, msg: str, stage: str) -> str:
    # Deterministic "fingerprint" to detect repeats. Avoid heavy hashing libs.
    return "{}|{}|{}".format(etype or "", _truncate(msg or "", 80) or "", stage or "")


def record_crash(exc, *, stage=None, ts=None, wifi=None, rssi=None):
    """
    Record last crash + increment count if same crash repeats.
    Safe to call from exception handlers (best-effort).
    """
    etype = exc.__class__.__name__ if exc else "Exception"
    msg = _truncate(str(exc) if exc else "", MAX_MSG_LEN)
    tb = _format_traceback_tail(exc) if exc else None

    stage_name = None
    stage_ms = None
    if isinstance(stage, dict):
        stage_name = stage.get("stage")
        stage_ms = stage.get("t_ms")

    if ts is None:
        ts = time.time()

    crash = {
        "etype": etype,
        "msg": msg,
        "tb": tb,
        "stage": stage_name,
        "stage_ms": stage_ms,
        "uptime_ms": time.ticks_ms(),
        "mem_free": gc.mem_free(),
        "wifi": bool(wifi) if wifi is not None else None,
        "rssi": rssi,
        "ts": ts,
    }

    store = _rtc_load()
    prev_key = store.get(_KEY_CRASH_KEY)
    new_key = _make_crash_key(etype, msg, stage_name)

    if prev_key == new_key:
        store[_KEY_CRASH_COUNT] = int(store.get(_KEY_CRASH_COUNT, 1) or 1) + 1
    else:
        store[_KEY_CRASH_COUNT] = 1
        store[_KEY_CRASH_KEY] = new_key

    store[_KEY_CRASH_LAST] = crash

    # Try write; if too big, drop tb and retry once.
    if not _rtc_save(store):
        try:
            crash["tb"] = None
            store[_KEY_CRASH_LAST] = crash
            _rtc_save(store)
        except Exception:
            pass


def peek_crash():
    store = _rtc_load()
    last = store.get(_KEY_CRASH_LAST)
    if not last:
        return None, 0
    count = int(store.get(_KEY_CRASH_COUNT, 1) or 1)
    return last, count


def clear_crash():
    store = _rtc_load()
    store.pop(_KEY_CRASH_LAST, None)
    store.pop(_KEY_CRASH_COUNT, None)
    store.pop(_KEY_CRASH_KEY, None)
    _rtc_save(store)
