"""All the tunable numbers for the CCCARE tracker live here.

Change values in this file after testing with real reps; no other file needs editing.
"""

# ---------------------------------------------------------------------------
# Sensor (ADXL335 at 3.3 V, read by the ESP32 in millivolts)
# ---------------------------------------------------------------------------
ZERO_G_MV = 1650.0      # output voltage at 0 g (about half of 3.3 V)
MV_PER_G = 330.0        # sensitivity: about 330 mV per g
BAUD_RATE = 115200
HELLO_PREFIX = "HELLO,CCCARE"
NO_DATA_TIMEOUT_S = 1.0  # more than this with no samples = "Sensor disconnected"

# ---------------------------------------------------------------------------
# Sensor health checks (same as the old firmware)
# ---------------------------------------------------------------------------
MIN_AXIS_V = 0.5
MAX_AXIS_V = 2.8
MIN_G_TOTAL = 0.3
MAX_G_TOTAL = 3.0
FAULT_AFTER_MS = 1000     # bad readings for this long -> fault
RECOVER_AFTER_MS = 1000   # good readings for this long -> recovered

# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
CAL_WAIT_MS = 3000        # countdown before each position is measured
CAL_MEASURE_MS = 1000     # how long each position is measured
MAX_WOBBLE_G = 0.25       # allowed movement on any axis while measuring
MIN_RANGE_DEG = 30.0      # start and end positions must be at least this far apart

# ---------------------------------------------------------------------------
# Rep counting defaults (percent of the calibrated range, 0% = start, 100% = end)
# ---------------------------------------------------------------------------
FILTER_ALPHA = 0.2        # EMA smoothing (smaller = smoother, slower)

DEFAULT_THRESHOLDS = {
    "top_pct": 85.0,        # reached the end position
    "start_pct": 25.0,      # a rep has started
    "bottom_pct": 15.0,     # back at the start position (fully extended)
    "left_top_pct": 60.0,   # dropped below this = lowering from the top
    "min_rep_ms": 400,      # faster than this = noise, ignored
    "rep_timeout_ms": 20000,  # a rep taking longer than this is dropped
    # Momentum detection (M5)
    "momentum_g_dev": 0.45,   # |g| this far from 1 g while lifting = swinging
    "min_lift_s": 0.4,        # bottom->top faster than this = swinging (tune with raw data)
}

# ---------------------------------------------------------------------------
# Exercises: sensor placement and per-exercise thresholds.
# Anything in "thresholds" overrides DEFAULT_THRESHOLDS for that exercise.
# ---------------------------------------------------------------------------
EXERCISES = {
    "Bicep curl": {
        "sensor": "forearm",
        "start": "arm straight down",
        "end": "fully curled",
        "hint": "Strap the sensor on the forearm near the wrist. Palm faces up as you curl.",
        "thresholds": {},
    },
    "Hammer curl": {
        "sensor": "forearm (thumb up)",
        "start": "arm straight down",
        "end": "fully curled",
        "hint": "Sensor on the forearm near the wrist. Palm faces your body, thumb up.",
        "thresholds": {},
    },
    "Lateral raise": {
        "sensor": "upper arm",
        "start": "arm at your side",
        "end": "arm out horizontal",
        "hint": "Strap the sensor on the outside of the upper arm, halfway between shoulder and elbow.",
        "thresholds": {},
    },
    "Dumbbell overhead press": {
        "sensor": "upper arm",
        "start": "dumbbell at shoulder",
        "end": "arm straight up",
        "hint": "Sensor on the upper arm (the forearm stays vertical, so it can't be used here).",
        "thresholds": {},
    },
    "Dumbbell bench press": {
        "sensor": "upper arm",
        "start": "dumbbell at chest",
        "end": "arm extended",
        "hint": "Sensor on the upper arm, lying on the bench (the forearm stays vertical).",
        "thresholds": {},
    },
    "Dumbbell row": {
        "sensor": "upper arm",
        "start": "arm hanging",
        "end": "elbow pulled back",
        "hint": "Sensor on the back of the upper arm. Brace your other hand on a bench.",
        "thresholds": {},
    },
}

ARMS = ("Left", "Right")


def thresholds_for(exercise):
    """Default thresholds merged with any overrides for this exercise."""
    t = dict(DEFAULT_THRESHOLDS)
    t.update(EXERCISES.get(exercise, {}).get("thresholds", {}))
    return t


# ---------------------------------------------------------------------------
# Workout plan defaults
# ---------------------------------------------------------------------------
DEFAULT_SETS = 3
DEFAULT_REPS = 10
DEFAULT_REST_S = 60
MIN_REST_S = 15
MAX_REST_S = 180

MAX_NAME_LEN = 20
