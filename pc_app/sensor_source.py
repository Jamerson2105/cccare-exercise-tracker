"""Where the sensor samples come from: the ESP32 over USB serial, or a simulator.

Both run on a background thread and put samples on a queue:
    ("sample", esp_millis, x_mV, y_mV, z_mV)
    ("status", text)          connection status messages for the UI

The UI thread empties the queue with root.after(), so the UI never blocks.
"""

import math
import queue
import random
import threading
import time

import serial
import serial.tools.list_ports

import config


def parse_line(line):
    """Parse 'D,<millis>,<x>,<y>,<z>'. Returns (millis, x, y, z) or None if corrupt."""
    parts = line.strip().split(",")
    if len(parts) != 5 or parts[0] != "D":
        return None
    try:
        ms, x, y, z = (int(p) for p in parts[1:])
    except ValueError:
        return None
    if not all(0 <= v <= 3500 for v in (x, y, z)):
        return None
    return ms, x, y, z


def list_ports():
    """Serial ports that could be the ESP32, USB ones first, e.g. ['COM6', 'COM3'].

    Bluetooth serial ports are skipped: probing them is slow and the tracker uses USB.
    """
    ports = [p for p in serial.tools.list_ports.comports()
             if "BTHENUM" not in (p.hwid or "").upper()
             and "bluetooth" not in (p.description or "").lower()]
    ports.sort(key=lambda p: 0 if "USB" in (p.hwid or "").upper() else 1)
    return [p.device for p in ports]


class SerialSource(threading.Thread):
    """Reads the ESP32. If port is None, it tries every port to find the tracker."""

    def __init__(self, out_queue, port=None):
        super().__init__(daemon=True)
        self.q = out_queue
        self.port = port
        self.stop_event = threading.Event()
        self.connected_port = None

    def stop(self):
        self.stop_event.set()

    def _status(self, text):
        self.q.put(("status", text))

    def _probe(self, name):
        """Open a port and check that a CCCARE tracker is talking on it."""
        try:
            ser = serial.Serial()
            ser.port = name
            ser.baudrate = config.BAUD_RATE
            ser.timeout = 0.2
            # Release DTR/RTS before opening. On boards with the auto-reset circuit
            # (e.g. Freenove WROVER + CH340), opening with them asserted holds the
            # ESP32 in reset, so it never sends anything.
            ser.dtr = False
            ser.rts = False
            ser.open()
            # Pulse RTS (wired to EN) to restart the ESP32 so it sends HELLO right away
            ser.rts = True
            time.sleep(0.1)
            ser.rts = False
        except (serial.SerialException, OSError):
            return None
        deadline = time.time() + 2.5    # opening the port resets the ESP32; give it time to boot
        good_lines = 0
        while time.time() < deadline and not self.stop_event.is_set():
            try:
                line = ser.readline().decode("ascii", errors="ignore")
            except (serial.SerialException, OSError):
                break
            if line.startswith(config.HELLO_PREFIX):
                return ser
            if parse_line(line):
                good_lines += 1
                if good_lines >= 5:
                    return ser
        ser.close()
        return None

    def _connect(self):
        candidates = [self.port] if self.port else list_ports()
        if not candidates:
            self._status("No serial ports found. Plug in the ESP32.")
            return None
        for name in candidates:
            if self.stop_event.is_set():
                return None
            self._status(f"Looking for the tracker on {name}...")
            ser = self._probe(name)
            if ser:
                self.connected_port = name
                self._status(f"Connected on {name}")
                return ser
        self._status("Tracker not found. Check the USB cable, or pick the port and press Reconnect.")
        return None

    def run(self):
        while not self.stop_event.is_set():
            ser = self._connect()
            if ser is None:
                self.stop_event.wait(3.0)    # try again in a few seconds
                continue
            try:
                while not self.stop_event.is_set():
                    raw = ser.readline()
                    if not raw:
                        continue
                    sample = parse_line(raw.decode("ascii", errors="ignore"))
                    if sample:                 # corrupt or HELLO lines are skipped
                        self.q.put(("sample",) + sample)
            except (serial.SerialException, OSError):
                self._status(f"Lost connection on {self.connected_port}. Reconnecting...")
            finally:
                try:
                    ser.close()
                except Exception:
                    pass
            self.stop_event.wait(1.0)


class DemoSource(threading.Thread):
    """Simulated sensor for testing without hardware (and as a symposium backup).

    The app sets .mode so the simulated person does the right thing:
      "idle"       arm resting at the start position
      "start"      holding the start position (calibration)
      "end"        holding the end position (calibration)
      "reps"       doing reps: mostly good ones, with an occasional incomplete
                   or swinging rep
    """

    SPAN_DEG = 120.0    # the simulated person's range of motion
    HZ = 50

    def __init__(self, out_queue):
        super().__init__(daemon=True)
        self.q = out_queue
        self.mode = "idle"
        self.stop_event = threading.Event()
        self.rep_index = 0

    def stop(self):
        self.stop_event.set()

    def _send(self, ms, angle_deg, extra_g=0.0):
        """Turn an arm angle into the three axis voltages the ESP32 would send."""
        a = math.radians(angle_deg)
        g = (0.02, math.sin(a), math.cos(a))
        scale = 1.0 + extra_g
        mv = [config.ZERO_G_MV + config.MV_PER_G * (c * scale) + random.gauss(0, 4) for c in g]
        self.q.put(("sample", ms, int(mv[0]), int(mv[1]), int(mv[2])))

    def _rep_profile(self):
        """Returns (peak %, lift seconds, lower seconds, swing) for the next rep."""
        self.rep_index += 1
        if self.rep_index % 7 == 4:
            return 0.70, 1.0, 1.0, False          # didn't reach the top
        if self.rep_index % 7 == 6:
            return 1.0, 0.3, 1.0, True            # swinging, too fast
        return random.uniform(0.95, 1.0), random.uniform(1.1, 1.5), random.uniform(1.0, 1.4), False

    def run(self):
        self.q.put(("status", "Demo mode: simulated sensor"))
        start = time.time()
        next_t = start
        rep = None          # (t0, peak, lift, lower, swing)
        while not self.stop_event.is_set():
            now = time.time()
            ms = int((now - start) * 1000)
            angle, extra = 0.0, 0.0
            if self.mode == "end":
                angle = self.SPAN_DEG
            elif self.mode == "reps":
                if rep is None:
                    rep = (now + 0.6,) + self._rep_profile()     # rest briefly first
                t0, peak, lift, lower, swing = rep
                t = now - t0
                hold = 0.25
                if t < 0:
                    frac = 0.0
                elif t < lift:
                    frac = peak * (1 - math.cos(math.pi * t / lift)) / 2
                    if swing and 0.05 < t < 0.2:
                        extra = 0.8          # jerk: extra acceleration on top of gravity
                elif t < lift + hold:
                    frac = peak
                elif t < lift + hold + lower:
                    frac = peak * (1 + math.cos(math.pi * (t - lift - hold) / lower)) / 2
                else:
                    frac = 0.0
                    rep = None
                angle = frac * self.SPAN_DEG
            else:
                rep = None
            self._send(ms, angle, extra)
            next_t += 1.0 / self.HZ
            delay = next_t - time.time()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.time()


def make_queue():
    return queue.Queue()
