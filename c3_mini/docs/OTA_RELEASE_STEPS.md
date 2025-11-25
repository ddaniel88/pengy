# OTA release steps (Pengy)

Ovaj dokument opisuje ritual za izbacivanje nove OTA verzije firmware-a
preko GitHub HTTPS manifesta.

## 1. Priprema koda na uređaju

1. Izmeni `main.py`, `uploader/pengy_api.py` i ostale fajlove po potrebi.
2. Ažuriraj konstantu `FIRMWARE_VERSION` u `main.py`:

   ```python
   FIRMWARE_VERSION = "YYYY-MM-DD_nn"
