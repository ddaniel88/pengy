# ota/ota_manager.py

import os
import gc
import ujson
import machine

import urequests
import uhashlib

from ota import ota_state

MANIFEST_FILE = "ota_manifest.json"

# Hardkodirani URL za sada – promeni ga kad budeš imao pravi server.
# Po želji kasnije dodaj u config["ota"]["manifest_url"] pa čitaj odatle.
DEFAULT_MANIFEST_URL = "https://raw.githubusercontent.com/ddaniel88/pengy/refs/heads/C3mini-SEN55/ota/latest/manifest.json"


# ---------------------------------------------------------------------------
# Pomoćne funkcije oko path-a

def _split_path(path):
    sep_index = path.rfind("/")
    if sep_index == -1:
        return ".", path
    dir_name = path[:sep_index]
    file_name = path[sep_index + 1:]
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


# ---------------------------------------------------------------------------
# Manifest: lokalno čitanje / upis

def _load_manifest_local():
    try:
        with open(MANIFEST_FILE, "r") as f:
            data = ujson.loads(f.read())
        return data
    except Exception as exc:
        print("OTA: cannot load local manifest:", exc)
        gc.collect()
        return None


def _save_manifest_local(manifest):
    try:
        with open(MANIFEST_FILE, "w") as f:
            f.write(ujson.dumps(manifest))
    except Exception as exc:
        print("OTA: cannot save manifest:", exc)
    finally:
        gc.collect()


# ---------------------------------------------------------------------------
# Manifest download preko HTTP

def _get_manifest_url(config=None):
    """
    Prioritet:
      1) ako postoji config["ota"]["manifest_url"] i nije prazan -> koristi to
      2) u suprotnom, koristi channel (latest / beta) i odgovarajući URL
      3) ako ništa nema, padni na DEFAULT_MANIFEST_URL
    """
    if not config:
        return DEFAULT_MANIFEST_URL

    ota_cfg = config.get("ota") or {}

    # 1) direktni override, ako je podešen
    direct = (ota_cfg.get("manifest_url") or "").strip()
    if direct:
        return direct

    # 2) kanal: latest / beta
    channel = (ota_cfg.get("channel") or "latest").lower()

    latest_url = (ota_cfg.get("latest_manifest_url") or "").strip()
    beta_url = (ota_cfg.get("beta_manifest_url") or "").strip()

    if channel == "beta" and beta_url:
        return beta_url

    if latest_url:
        return latest_url

    # 3) fallback
    return DEFAULT_MANIFEST_URL


def _download_manifest_http(url):
    print("OTA: downloading manifest from", url)
    try:
        gc.collect()
        resp = urequests.get(url)
        if resp.status_code != 200:
            print("OTA: manifest HTTP status:", resp.status_code)
            try:
                resp.close()
            except:
                pass
            return None

        text = resp.text
        try:
            resp.close()
        except:
            pass

        try:
            manifest = ujson.loads(text)
        except Exception as exc:
            print("OTA: cannot parse manifest JSON:", exc)
            gc.collect()
            return None

        # Sačuvaj lokalno za debug
        try:
            _save_manifest_local(manifest)
        except Exception:
            pass

        return manifest

    except Exception as exc:
        print("OTA: manifest download failed:", exc)
        gc.collect()
        return None


# ---------------------------------------------------------------------------
# Provera prostora

def _has_enough_space(total_size, safety_factor=2.0):
    """
    Vrlo gruba procena da li imamo dovoljno mesta za OTA.
    total_size = zbir veličina svih fajlova iz manifesta.
    """
    try:
        st = os.statvfs("/")
        free_bytes = st[3] * st[0]
        print("OTA: free space =", free_bytes, "needed ~", int(total_size * safety_factor))
        return free_bytes > int(total_size * safety_factor)
    except Exception as exc:
        print("OTA: statvfs failed:", exc)
        # bolje "optimistički" za sada
        return True


# ---------------------------------------------------------------------------
# Download pojedinačnog fajla sa SHA256 proverom

def _make_final_url(manifest, file_entry):
    url = file_entry.get("url")
    if not url:
        return None

    # Ako je apsolutan URL – koristi ga direktno
    if url.startswith("http://") or url.startswith("https://"):
        return url

    # Inače sklapa se sa base_url
    base = manifest.get("base_url") or ""
    if not base:
        # base nema → pretpostavi da je url već pun
        return url

    if not base.endswith("/"):
        base += "/"

    return base + url

def _download_file_http(file_entry):
    """
    file_entry:
      {
        "path": "uploader/pengy_api.py",
        "url": "http://.../pengy_api.py.ota",
        "size": 2048,
        "sha256": "aabbcc..."
      }
    """
    path = file_entry.get("path")
    manifest = file_entry.get("_manifest")
    url = _make_final_url(manifest, file_entry)
    expected_sha = (file_entry.get("sha256") or "").lower()
    size = int(file_entry.get("size") or 0)

    if not path or not url:
        print("OTA: file entry missing path or url:", file_entry)
        return False

    dir_name, base_name = _split_path(path)
    tmp_path = _join_path(dir_name, base_name + ".ota")

    print("OTA: downloading file", path, "from", url)

    try:
        gc.collect()
        resp = urequests.get(url)
    except Exception as exc:
        print("OTA: HTTP error while downloading", path, ":", exc)
        gc.collect()
        return False

    # Kreiraj dir ako treba (u praksi već postoji)
    if dir_name != "." and dir_name not in os.listdir():
        try:
            os.mkdir(dir_name)
        except Exception as exc:
            print("OTA: cannot create dir", dir_name, ":", exc)
            try:
                resp.close()
            except:
                pass
            return False

    h = uhashlib.sha256()
    written = 0

    try:
        with open(tmp_path, "wb") as f:
            # čitaj u manjim chunk-ovima da ne pojede RAM
            while True:
                chunk = resp.raw.read(1024)
                if not chunk:
                    break
                written += len(chunk)
                h.update(chunk)
                f.write(chunk)
        try:
            resp.close()
        except:
            pass
    except Exception as exc:
        print("OTA: file write failed for", path, ":", exc)
        try:
            resp.close()
        except:
            pass
        gc.collect()
        return False

    # Provera veličine (ako je zadat size)
    if size and written != size:
        print("OTA: size mismatch for", path, "expected", size, "got", written)
        return False

    # Provera sha256 (ako je zadato)
    if expected_sha:
        digest = h.digest()
        hex_digest = "".join("{:02x}".format(b) for b in digest)
        if hex_digest.lower() != expected_sha:
            print("OTA: sha256 mismatch for", path)
            print(" expected:", expected_sha)
            print("   actual:", hex_digest)
            return False

    print("OTA: downloaded OK:", path, "(", written, "bytes )")
    return True


def _download_all_files(manifest):
    """
    Za svaki file iz manifest["files"]:
      - preuzmi .ota fajl preko HTTP
      - proveri size i sha256 ako su zadati
    """
    files = manifest.get("files") or []
    if not files:
        print("OTA: manifest has no files.")
        return False

    for f in files:
        f["_manifest"] = manifest  # privremeno
        ok = _download_file_http(f)
        del f["_manifest"]
        if not ok:
            return False

    return True


# ---------------------------------------------------------------------------
# ACTIVATE faza – backup + rename (koristiš već proverenu varijantu)

def _activate_all_files(manifest):
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
                # rollback ako možemo
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


# ---------------------------------------------------------------------------
# Zajednička logika za update na bazi manifesta (bilo lokalnog, bilo HTTP)

def _total_size_from_manifest(manifest):
    total = manifest.get("total_size")
    if total is not None:
        return int(total)
    total = 0
    for f in manifest.get("files") or []:
        total += int(f.get("size") or 0)
    return total


def _do_update_with_manifest(manifest):
    state = ota_state.load_state()

    device_type = manifest.get("device_type")
    firmware_version = manifest.get("firmware_version")
    total_size = _total_size_from_manifest(manifest)

    current_version = state.get("current_version", "unknown")
    if firmware_version is None:
        print("OTA: manifest has no firmware_version")
        ota_state.set_error("no_version")
        return False

    # ako je current_version "unknown" – tretiraj kao uvek starije
    if current_version != "unknown" and firmware_version <= current_version:
        print("OTA: no newer firmware:", firmware_version, "<=", current_version)
        return False

    if not _has_enough_space(total_size):
        print("OTA: not enough space for OTA.")
        ota_state.set_error("no_space")
        return False

    # imamo novu verziju i mesta – krećemo
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

    # sve fajlove smo aktivirali – prelazimo u TRY_UPDATE i reset
    ota_state.begin_try_update()

    print("OTA: update applied, resetting...")
    machine.reset()
    return True


# ---------------------------------------------------------------------------
# Javni API

def start_update(config=None):
    """
    Glavni OTA ulaz:
      - skini manifest sa HTTP-a
      - uradi sve faze (download, activate, reset)
    """
    url = _get_manifest_url(config)
    manifest = _download_manifest_http(url)
    if not manifest:
        print("OTA: no manifest, abort.")
        ota_state.set_error("no_manifest")
        return False

    return _do_update_with_manifest(manifest)


def start_update_from_manifest():
    """
    Alternativni ulaz za debug:
      - manifest mora već da postoji lokalno kao ota_manifest.json
    """
    manifest = _load_manifest_local()
    if not manifest:
        print("OTA: no local manifest, abort.")
        ota_state.set_error("no_manifest_local")
        return False

    return _do_update_with_manifest(manifest)
