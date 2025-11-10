# boot.py
import os

UPDATE_MAP = {
    #"update_main.py": "main.py",
    "update_sensor_community.py": "uploader/sensor_community.py",
    "update_pengy_api.py": "uploader/pengy_api.py",
    "update_setup.py": "setup.py",
}

def file_exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False

def ensure_dir(path):
    # za slučaj da /uploader ne postoji
    parts = path.split("/")
    if len(parts) > 1:
        d = parts[0]
        try:
            os.mkdir(d)
        except OSError:
            pass

def copy_file(src, dst):
    # napravi dir ako treba
    ensure_dir(dst)
    with open(src, "r") as fsrc:
        data = fsrc.read()
    with open(dst, "w") as fdst:
        fdst.write(data)

def apply_updates():
    for update_name, target_name in UPDATE_MAP.items():
        if file_exists(update_name):
            print("Applying update:", update_name, "->", target_name)
            copy_file(update_name, target_name)
            os.remove(update_name)

apply_updates()
# posle ovoga uređaj normalno radi import main
