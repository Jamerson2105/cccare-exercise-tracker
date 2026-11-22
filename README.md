# CCCARE Exercise Tracker

A wearable exercise tracker (ECE 198). An ADXL335 accelerometer strapped to the arm is read by an
ESP32, which streams the readings over USB to a Windows laptop. A Python desktop app on the laptop
does everything else:

- users and saved calibrations (per user, exercise and arm)
- rep counting with full-rep checks (didn't reach the top, didn't fully extend)
- momentum ("swinging") warnings
- workout plan: target sets and reps, rest timer
- spoken feedback (text-to-speech), plus a mute toggle
- history table, CSV export and progress charts
- demo mode, which simulates the sensor so the app runs with no hardware

## What's in this folder

| Path | What it is |
|---|---|
| `cccare_tracker/cccare_tracker.ino` | ESP32 firmware: streams sensor readings over USB serial |
| `pc_app/app.py` | The desktop app (start here) |
| `pc_app/config.py` | All tunable numbers: thresholds, exercises, sensor constants |
| `pc_app/rep_counter.py` | Rep counting, calibration and sensor-health logic (no UI) |
| `pc_app/db.py` | SQLite database (`pc_app/tracker.db`, created automatically) |
| `pc_app/sensor_source.py` | Serial reader and the demo-mode simulator |
| `pc_app/tts.py` | Text-to-speech on a background thread |
| `pc_app/manual.py` | The built-in Guide tab and the exercise pictures |
| `pc_app/tests/` | pytest tests for the rep logic |
| `docs/exercise_manual.html` | Illustrated guide: sensor placement and START/END poses for each exercise |

## Wiring (Freenove ESP32-WROVER)

| ADXL335 | ESP32 |
|---|---|
| VCC | 3V3 |
| GND | GND |
| X | GPIO 34 |
| Y | GPIO 35 |
| Z | GPIO 32 |

These are ADC1 pins, which work alongside USB. On an ESP32-S3, use X → 1, Y → 2, Z → 3 (the firmware
picks these automatically). Unplug the WROVER camera module if it's attached.

## Upload the firmware

1. Open `cccare_tracker/cccare_tracker.ino` in the Arduino IDE.
2. Tools > Board > esp32 > **ESP32 Wrover Module** (esp32 board package 3.0.7 or newer), and pick the COM port.
3. Upload. If it gets stuck on "Connecting...", hold the BOOT button. No extra libraries are needed.
4. Optional check: in the Serial Monitor at 115200 baud you should see `HELLO,CCCARE,1`, then
   lines like `D,12345,1650,1660,1980` about 50 times a second. **Close the Serial Monitor before you
   start the app**, because only one program can use the port at a time.

## Install and run the app (Windows, Python 3.14)

```
py -m pip install -r pc_app/requirements.txt
py pc_app/app.py
```

- `py pc_app/app.py --demo` starts in demo mode (no hardware).
- `py -m pytest pc_app/tests` runs the tests.
- `py pc_app/app.py --selftest` runs a scripted demo workout and exits (an end-to-end check).

## Build a Windows .exe

```
build_exe.bat
```

This creates `dist\CCCARE Tracker.exe`, a single file you can copy to any Windows laptop without
installing Python. The database (`tracker.db`) and `raw_data\` are saved in the same folder as the
.exe, so keep it in a folder you can write to (not `Program Files`). Run `build_exe.bat` again after any
code change. Windows SmartScreen may warn the first time because the exe isn't signed: click
**More info → Run anyway**.

## Sensor placement

The app measures how much the sensor tilts, so the sensor has to go on the part of the arm that
rotates. For presses, the forearm stays roughly vertical, so the sensor goes on the **upper arm**.

| Exercise | Sensor on | Start → end |
|---|---|---|
| Bicep curl | forearm | arm straight down → fully curled |
| Hammer curl | forearm (thumb up) | arm straight down → fully curled |
| Lateral raise | upper arm | arm at side → arm out horizontal |
| Overhead press | upper arm | dumbbell at shoulder → arm straight up |
| Bench press | upper arm | dumbbell at chest → arm extended |
| Dumbbell row | upper arm | arm hanging → elbow pulled back |

## How to use the app

The app has a **Guide** tab with the full manual built in: pictures of the START and END
positions for each exercise, where to strap the sensor, and what to do when something goes wrong.


The **Workout** tab walks you through three pages, one after the other (**Next** / **Back**):

1. Plug in the ESP32. The app finds it automatically: the top bar shows "Sensor connected" in
   green. If it doesn't connect, open **Settings**, pick the COM port and press **Reconnect**.
   No hardware? Tick **Demo mode** in **Settings**.
2. **Setup page:** press the **person** (**+ Add person** for a new one; names are 1–20 characters,
   no duplicates), the **exercise**, the **arm**, and set **sets**, **reps per set** and **rest**
   (defaults: 3 × 10, 60 s). Press **Next**.
3. **Calibrate page:** the pictures show where to strap the sensor. You only calibrate once per
   person, exercise and arm, because it's saved. Follow the voice: hold the start position
   still, then hold the end position still. Calibration fails, and keeps your old one, if you
   move while it's measuring or the range is under 30°. The button becomes **Cancel calibration**
   while it runs. Reconnecting the sensor stops a running calibration so you can start it again.
   Press **Next**.
4. **Exercise page:** press **Start Set** and do your reps. Each full rep is counted out loud.
   Incomplete reps and swinging are announced but not counted. The set ends automatically at the
   target, or press **Stop Set**. The arm angle is hidden unless **Show arm angle** is ticked in
   **Settings**.
5. The **rest timer** starts on its own and counts down the last 10 s aloud. You can press **Skip Rest**.
   Press **Start Set** for the next set. After the last set, a summary appears.
6. **History** tab: past sets, with filters, **Export CSV**, and **Clear this user's history**.
   **Progress** tab: charts of reps, range of motion and momentum per session, plus a left-vs-right
   comparison once both arms have data.

**Record raw data** saves every sample to `pc_app/raw_data/raw_<date>.csv`. Use it to tune the
thresholds in `pc_app/config.py` with real reps.

## Built-in checks

- **Sensor problems** (loose wire, short, bad power): if any axis reads outside 0.5–2.8 V, or the total
  is outside 0.3–3.0 g, for 1 s, a red banner appears and the app says "Sensor problem".
  Calibration and counting pause until the readings are normal for 1 s.
- **Unplugged / no data for 1 s**: "Sensor disconnected". Counting pauses, and the app reconnects by itself.
- **Stalled reps** that take longer than 20 s are dropped.
- **Start Set is blocked** (with the reason) if the exercise isn't calibrated or the sensor isn't working.
