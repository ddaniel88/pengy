# boards/selector.py

def load_board_config(device_cfg: dict):
    """
    device_cfg is config['device'] (device_meta from main.py)
    expecting device_cfg['board'] example: 'c3_lolin', 'esp32s3_n16r8', 'esp32d'
    """
    board = (device_cfg.get("board") or "").strip().lower()

    # Eksplicitne mape (profil po pločici)
    if board == "c3_lolin":
        from boards import board_config_c3_lolin as cfg
        return cfg

    if board in ("esp32s3_n16r8", "s3_n16r8", "esp32s3"):
        # short values accepted, prefer full name, for example esp32s3_n16r8
        from boards import board_config_esp32s3_n16r8 as cfg
        return cfg

    if board == "esp32d":
        from boards import board_config_esp32d as cfg
        return cfg

    # Fallback: c3_lolin is default (safest)
    from boards import board_config_c3_lolin as cfg
    return cfg
