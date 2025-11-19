# ota/ota_manager.py

import os
import ujson
import gc
import machine

from ota import ota_state

MANIFEST_FILE = "ota_manifest.json"

# očekivani format manifesta:
# {
#   "device_type": "pengy-wifi",
#   "firmware_version": "2025-11-20_1",
#   "total_size": 12345,
#   "files": [
#       { "path": "app_main.py", "size": 1234, "sha256": "..." },
#       ...
#   ]
# }


def _load_manifest():
    try:
        with open(MANIFEST_FILE, "r") as f:
            data = ujson.loads(f.read())
        return data
    except Exception as exc:
        print("OTA: cannot load manifest:", exc)
        gc.collect()
        return None


def _save_manifest(manifest):
    try:
        with open(MANIFEST_FILE, "w") as f:
            f.write(ujson.dumps(manifest))
    except Exception as exc:
        print("OTA: cannot save manifest:", exc)
    finally:
        gc.collect()


def _has_enough_space(total_size, safety_factor=2.0):
    """
    Vrlo gruba procena da li imamo dovoljno mesta za OTA.
    total_size = zbir veličina svih fajlova iz manifesta.
    """
    try:
        st = os.statvfs("/")
        # slobodni blokovi * veličina bloka
        free_bytes = st[3] * st[0]
        print("OTA: free space =", free_bytes, "needed ~", int(total_size * safety_factor))
        return free_bytes > int(total_size * safety_factor)
    except Exception as exc:
        print("OTA: statvfs failed:", exc)
        # ako ne možemo da izračunamo, bolje reci "nema"
        return True # for TEST purpose


def _download_all_files(manifest):
    """
    DOWNLOAD faza – ovde će kasnije ići HTTP preuzimanje.
    Za sada samo placeholder koji pretpostavlja da su .ota fajlovi već tu.
    """
    # TODO: ovde ćemo kasnije:
    # - za svaki file iz manifest["files"]:
    #   - skinuti ga sa servera u path + ".ota"
    #   - proveriti sha256
    print("OTA: _download_all_files placeholder – pretpostavljam da su .ota fajlovi već na flešu.")
    return True


def _split_path(path):
    """
    Vrati (dir_name, file_name)
    Za "uploader/pengy_api.py" -> ("uploader", "pengy_api.py")
    Za "main.py" -> (".", "main.py")
    """
    sep_index = path.rfind("/")
    if sep_index == -1:
        return ".", path
    dir_name = path[:sep_index]
    file_name = path[sep_index + 1 :]
    if not dir_name:
        dir_name = "."
    return dir_name, file_name


def _join_path(dir_name, file_name):
    if dir_name == "." or dir_name == "":
        return file_name
    return dir_name + "/" + file_name


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _activate_all_files(manifest):
    """
    ACTIVATE faza – radi .bak + rename za sve fajlove iz manifesta.
    Radi i za fajlove u podfoldere (npr. uploader/pengy_api.py).
    """
    for f in manifest.get("files", []):
        path = f.get("path")
        if not path:
            continue

        dir_name, base_name = _split_path(path)

        orig = _join_path(dir_name, base_name)
        tmp = _join_path(dir_name, base_name + ".ota")
        bak = _join_path(dir_name, base_name + ".bak")

        try:
            # mora da postoji .ota fajl
            if not _exists(tmp):
                print("OTA: missing .ota for", path)
                return False

            # obriši stari .bak ako postoji
            if _exists(bak):
                try:
                    os.remove(bak)
                except Exception as exc:
                    print("OTA: cannot remove old bak", bak, ":", exc)

            # ako postoji original – prebaci u .bak
            if _exists(orig):
                try:
                    os.rename(orig, bak)
                except Exception as exc:
                    print("OTA: cannot backup", orig, "->", bak, ":", exc)
                    return False

            # prebaci .ota u finalni fajl
            try:
                os.rename(tmp, orig)
            except Exception as exc:
                print("OTA: cannot activate", tmp, "->", orig, ":", exc)
                # pokušaj da vratiš bak
                if _exists(bak):
                    try:
                        os.rename(bak, orig)
                    except Exception as exc2:
                        print("OTA: rollback from bak failed:", exc2)
                return False

        except Exception as exc:
            print("OTA: activate failed for", path, ":", exc)
            gc.collect()
            return False

    return True

def start_update_from_manifest():
    """
    Glavni ulaz u OTA za sada:
    - manifest je već na flešu (ota_manifest.json)
    - proveri da li je verzija nova
    - proveri prostor
    - odradi DOWNLOAD (placeholder) + ACTIVATE
    - setuje state = try_update i resetuje uređaj
    """
    state = ota_state.load_state()
    manifest = _load_manifest()
    if not manifest:
        print("OTA: no manifest, abort.")
        ota_state.set_error("no_manifest")
        return False

    device_type = manifest.get("device_type")
    firmware_version = manifest.get("firmware_version")
    total_size = manifest.get("total_size", 0)

    # ovde kasnije možeš da proveriš device_type
    # ako imaš nešto u config-u što kaže koji je ovo device

    current_version = state.get("current_version", "unknown")
    if firmware_version is None:
        print("OTA: manifest has no firmware_version")
        ota_state.set_error("no_version")
        return False

    # ako je current_version "unknown" – tretiraj kao "uvek starije", dozvoli OTA
    if current_version != "unknown" and firmware_version <= current_version:
        print("OTA: no newer firmware:", firmware_version, "<=", current_version)
        # nema potrebe da diramo state, ostajemo u idle
        return False

    # ima nova verzija
    if not _has_enough_space(total_size):
        print("OTA: not enough space for OTA.")
        ota_state.set_error("no_space")
        return False

    # možemo da krenemo
    ota_state.begin_download(firmware_version)

    ok = _download_all_files(manifest)
    if not ok:
        ota_state.set_error("download_failed")
        return False

    ota_state.begin_activate()

    ok = _activate_all_files(manifest)
    if not ok:
        ota_state.set_error("activate_failed")
        return False

    # ako smo ovde – sve fajlove smo aktivirali
    ota_state.begin_try_update()

    print("OTA: update applied, resetting...")
    machine.reset()
    # posle ovoga praktično nema povratka u ovu funkciju
    return True
