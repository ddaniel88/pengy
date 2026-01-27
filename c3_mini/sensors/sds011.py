# sensors/sds011.py
# MicroPython SDS011 driver + sensor wrapper for Pengy
#
# Driver: UART framing + commands (no "policy").
# Wrapper (SDS011Sensor): minute sampling + adaptive sleep/work behavior.

import time
import machine


class SDS011Driver:
    """
    Low-level SDS011 protocol driver.
    - Parses AA C0 ... AB data frames from UART (stream-safe, resync).
    - Sends AA B4 ... AB commands (sleep/work, etc.).
    """

    FRAME_HEAD = 0xAA
    FRAME_TAIL = 0xAB
    CMD_HEAD = 0xAA
    CMD_TAIL = 0xAB

    def __init__(self, uart: machine.UART):
        self.uart = uart
        self._buf = bytearray()

    def _uart_read_into_buffer(self):
        try:
            n = self.uart.any()
        except Exception:
            n = 0
        if n and n > 0:
            chunk = self.uart.read(n)
            if chunk:
                self._buf.extend(chunk)

    @staticmethod
    def _checksum_data_frame(frame: bytes) -> int:
        # For AA C0 ... AB frames checksum is sum(frame[2:8]) & 0xFF
        return (sum(frame[2:8]) & 0xFF)

    def read_measurement(self, timeout_ms: int = 1200):
        """
        Reads one valid data frame and returns (pm25, pm10) floats.
        Returns None on timeout.
        """
        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
            self._uart_read_into_buffer()

            # Need at least 10 bytes: AA C0 d1 d2 d3 d4 id1 id2 checksum AB
            while len(self._buf) >= 10:
                # Resync to 0xAA
                if self._buf[0] != self.FRAME_HEAD:
                    del self._buf[0]
                    continue

                # Look for tail at position 9 for data frame
                if self._buf[9] != self.FRAME_TAIL:
                    # Not a valid aligned frame; shift by 1 and continue searching
                    del self._buf[0]
                    continue

                frame = bytes(self._buf[:10])
                del self._buf[:10]

                # Validate type byte
                if frame[1] != 0xC0:
                    continue

                checksum = frame[8]
                if self._checksum_data_frame(frame) != checksum:
                    continue

                pm25 = ((frame[3] << 8) | frame[2]) / 10.0
                pm10 = ((frame[5] << 8) | frame[4]) / 10.0
                return (pm25, pm10)

            time.sleep_ms(10)

        return None

    def _send_command(self, cmd_id: int, data0: int, data1: int = 0, dev_id1: int = 0xFF, dev_id2: int = 0xFF):
        """
        Sends AA B4 <cmd> <data0> <data1> 0...0 <id1> <id2> <checksum> AB
        Based on Nova Fitness SDS011 protocol guide.
        """
        # Command frame is 19 bytes total
        payload = bytearray(19)
        payload[0] = 0xAA
        payload[1] = 0xB4
        payload[2] = cmd_id

        # data bytes 3..14 (12 bytes). We use first two.
        payload[3] = data0 & 0xFF
        payload[4] = data1 & 0xFF
        for i in range(5, 15):
            payload[i] = 0x00

        payload[15] = dev_id1 & 0xFF
        payload[16] = dev_id2 & 0xFF

        checksum = sum(payload[2:17]) & 0xFF
        payload[17] = checksum
        payload[18] = 0xAB

        try:
            self.uart.write(payload)
        except Exception:
            # Ignore write errors; caller can retry later
            pass

    def set_sleep(self, sleep: bool):
        """
        Command 0x06 (sleep/work). According to protocol examples:
        - data0 = 0x01 (set mode)
        - data1 = 0x00 => sleep
        - data1 = 0x01 => work
        """
        self._send_command(cmd_id=0x06, data0=0x01, data1=(0x00 if sleep else 0x01))


class SDS011Sensor:
    """
    Pengy-friendly sensor wrapper.
    - Implements get_supported_fields() and measure_one_minute(...)
    - Uses adaptive sleep/work policy:
        * Default: sleep outside sampling
        * During sampling: wake -> short warmup -> take N samples -> (sleep unless "keep awake")
        * If last minute PM is high OR "suspect spike" is detected: keep awake for a hold window
    """

    def __init__(
        self,
        uart_id: int,
        tx_pin: int,
        rx_pin: int,
        *,
        baudrate: int = 9600,
        warmup_ms: int = 3000,
        read_timeout_ms: int = 1200,
        keep_awake_pm_threshold: float = 200.0,
        keep_awake_hold_s: int = 5 * 60,
        suspect_pm_threshold: float = 800.0,
        suspect_hold_s: int = 10 * 60,
        debug: bool = False
    ):
        self._uart = machine.UART(
            uart_id,
            baudrate=baudrate,
            bits=8,
            parity=None,
            stop=1,
            tx=machine.Pin(tx_pin),
            rx=machine.Pin(rx_pin),
        )
        # Some boards benefit from a slightly bigger RX buffer (if supported)
        try:
            self._uart.init(rxbuf=256)
        except Exception:
            pass

        self._drv = SDS011Driver(self._uart)

        self._warmup_ms = int(warmup_ms)
        self._read_timeout_ms = int(read_timeout_ms)

        self._keep_awake_pm_threshold = float(keep_awake_pm_threshold)
        self._keep_awake_hold_s = int(keep_awake_hold_s)

        self._suspect_pm_threshold = float(suspect_pm_threshold)
        self._suspect_hold_s = int(suspect_hold_s)

        self._debug = bool(debug)

        self._awake_until_ms = 0
        self._is_awake = False

        # Start in sleep to be gentle by default
        self.sleep()

    def get_supported_fields(self):
        return ["pm25", "pm10"]

    def _now_ms(self):
        return time.ticks_ms()

    def _set_awake_until(self, hold_s: int):
        until = time.ticks_add(self._now_ms(), int(hold_s) * 1000)
        self._awake_until_ms = until

    def wake(self):
        if self._debug:
            print("[SDS011] wake")
        self._drv.set_sleep(False)
        self._is_awake = True

    def sleep(self):
        if self._debug:
            print("[SDS011] sleep")
        self._drv.set_sleep(True)
        self._is_awake = False

    def _ensure_awake_for_sampling(self):
        now = self._now_ms()
        if time.ticks_diff(self._awake_until_ms, now) > 0:
            # Keep-awake window active
            if not self._is_awake:
                self.wake()
            return

        # Not in keep-awake window: wake just for sampling
        if not self._is_awake:
            self.wake()

    def _maybe_sleep_after_sampling(self):
        now = self._now_ms()
        if time.ticks_diff(self._awake_until_ms, now) > 0:
            # Keep awake
            return
        # Otherwise go to sleep
        self.sleep()

    def measure_one_minute(self, samples_count: int, interval_seconds: int, trim_extremes: bool):
        """
        Collects N samples. This is called from SensorManager.measure_minute_all(...)
        with interval_seconds=2 in your main.py.
        Returns dict {"pm25": float, "pm10": float} or None.
        """
        try:
            samples_count = int(samples_count or 0)
            if samples_count <= 0:
                samples_count = 1
        except Exception:
            samples_count = 1

        # Wake (or keep awake) and short warmup
        self._ensure_awake_for_sampling()
        if self._warmup_ms > 0:
            time.sleep_ms(self._warmup_ms)

        pm25_values = []
        pm10_values = []

        for _ in range(samples_count):
            res = self._drv.read_measurement(timeout_ms=self._read_timeout_ms)
            if res:
                pm25, pm10 = res
                # Basic sanity (avoid negative / nonsense)
                if pm25 >= 0 and pm10 >= 0:
                    pm25_values.append(pm25)
                    pm10_values.append(pm10)

            # Keep cadence consistent with the rest of the system
            if interval_seconds and interval_seconds > 0:
                time.sleep(interval_seconds)

        if not pm25_values or not pm10_values:
            self._maybe_sleep_after_sampling()
            return None

        # Optional trimming (simple, cheap)
        pm25 = self._aggregate(pm25_values, trim_extremes)
        pm10 = self._aggregate(pm10_values, trim_extremes)

        # Adaptive keep-awake policy:
        # - If pollution is high, keep awake for some minutes to reduce dust settling.
        # - If sensor spikes to very high values, keep awake longer (often it stabilizes).
        if (pm25 >= self._suspect_pm_threshold) or (pm10 >= self._suspect_pm_threshold):
            self._set_awake_until(self._suspect_hold_s)
            if self._debug:
                print("[SDS011] suspect spike -> keep awake", self._suspect_hold_s, "s")
        elif (pm25 >= self._keep_awake_pm_threshold) or (pm10 >= self._keep_awake_pm_threshold):
            self._set_awake_until(self._keep_awake_hold_s)
            if self._debug:
                print("[SDS011] high PM -> keep awake", self._keep_awake_hold_s, "s")

        self._maybe_sleep_after_sampling()

        return {"pm25": pm25, "pm10": pm10}

    @staticmethod
    def _aggregate(values, trim_extremes: bool):
        if not values:
            return None
        if not trim_extremes or len(values) < 3:
            return sum(values) / len(values)

        # Trim one min and one max (robust enough for N=5)
        vals = sorted(values)
        vals = vals[1:-1]
        if not vals:
            vals = values
        return sum(vals) / len(vals)
