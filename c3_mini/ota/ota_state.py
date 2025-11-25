# ota/ota_state.py

import ujson
import os
import gc
import time

STATE_FILE = "ota_state.json"

# dozvoljena stanja
STATE_IDLE = "idle"
STATE_DOWNLOAD = "download"
STATE_ACTIVATE = "activate"
STATE_TRY_UPDATE = "try_update"
STATE_ERROR = "error"


def _default_state():
    return {
        "current_version": "unknown",
        "pending_version": None,
        "state": STATE_IDLE,
        "attempt": 0,
        "last_error": None,
    }


def load_state():
    """
    Učitaj ota_state.json.
    Ako ne postoji ili je polomljen, vrati default state.
    """
    try:
        with open(STATE_FILE, "r") as f:
            data = ujson.loads(f.read())
            # malo sanity check-a
            if not isinstance(data, dict):
                raise ValueError("state not dict")
            if "state" not in data:
                raise ValueError("no state field")
            return data
    except Exception as exc:
        print("OTA state load failed, using default:", exc)
        gc.collect()
        return _default_state()


def _atomic_write(data):
    """
    Sigurnije upisivanje: piši u .tmp pa rename.
    """
    tmp_name = STATE_FILE + ".tmp"
    try:
        gc.collect()
        with open(tmp_name, "w") as f:
            f.write(ujson.dumps(data))
        # ako je sve OK, zameni stari fajl
        if STATE_FILE in os.listdir():
            os.remove(STATE_FILE)
        os.rename(tmp_name, STATE_FILE)
    except Exception as exc:
        print("OTA state save failed:", exc)
    finally:
        gc.collect()


def save_state(state):
    """
    Snimi ceo state dict.
    """
    if not isinstance(state, dict):
        return
    _atomic_write(state)


def ensure_initialized(current_version="unknown"):
    """
    Ako state fajl ne postoji, napravi ga.
    Ako postoji, samo osveži current_version ako je unknown.
    """
    state = load_state()
    if state["current_version"] == "unknown" and current_version:
        state["current_version"] = current_version
        save_state(state)
    elif state is _default_state():
        # nikad viđen, setuj verziju
        state["current_version"] = current_version
        save_state(state)
    return state


# --- pomoćne funkcije za menjanje stanja -----------------------------

def set_state(new_state, **updates):
    """
    Opšti helper: učitaj → izmeni → snimi.
    """
    state = load_state()
    state["state"] = new_state
    for k, v in updates.items():
        state[k] = v
    save_state(state)
    return state


def begin_download(pending_version):
    """
    Ulazak u DOWNLOAD fazu (nakon što je manifest skinut i prihvaćen).
    """
    return set_state(
        STATE_DOWNLOAD,
        pending_version=pending_version,
        attempt=0,
        last_error=None,
    )


def begin_activate():
    """
    Kada su svi .ota fajlovi skinuti i kreće ACTIVATE faza.
    """
    return set_state(STATE_ACTIVATE, last_error=None)


def begin_try_update():
    """
    Kada je ACTIVATE gotov i resetujemo uređaj u novu verziju.
    """
    state = load_state()
    attempt = state.get("attempt", 0) + 1
    state["attempt"] = attempt
    state["state"] = STATE_TRY_UPDATE
    state["last_error"] = None
    save_state(state)
    return state


def mark_successful(new_version):
    """
    Nova verzija je uspešno podignuta.
    """
    return set_state(
        STATE_IDLE,
        current_version=new_version,
        pending_version=None,
        attempt=0,
        last_error=None,
    )


def set_error(reason):
    """
    Označi da je OTA otišla u error stanje.
    Ne dira current_version.
    """
    return set_state(
        STATE_ERROR,
        last_error=reason,
    )
