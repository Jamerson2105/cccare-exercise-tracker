"""Rep counting, calibration and sensor health logic (no UI code).

Ported from the original cccare_tracker.ino, which was tested on real reps.

How it works:
  * Calibration records two gravity vectors: the START position ("down") and the
    END position ("up"). The angle between them is the range of motion (span).
  * During a set, the angle between the current (smoothed) gravity vector and the
    start vector, divided by the span, gives progress: 0% = start, 100% = end.
  * A small state machine watches progress to find full reps and incomplete ones.

All times are in milliseconds and come from the caller, so the logic can be tested
with made-up sequences.
"""

import math
from dataclasses import dataclass, field

import config

# ---------------------------------------------------------------------------
# Small vector helpers (a vector is a tuple (x, y, z) in g)
# ---------------------------------------------------------------------------


def mv_to_g(mv):
    """Convert one ADXL335 axis reading from millivolts to g."""
    return (mv - config.ZERO_G_MV) / config.MV_PER_G


def magnitude(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def angle_between(a, b):
    """Angle between two vectors in degrees (0 if either is ~zero)."""
    la, lb = magnitude(a), magnitude(b)
    if la < 0.01 or lb < 0.01:
        return 0.0
    c = (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (la * lb)
    c = max(-1.0, min(1.0, c))
    return math.degrees(math.acos(c))


# ---------------------------------------------------------------------------
# Sensor health
# ---------------------------------------------------------------------------


class SensorHealth:
    """Flags a fault after 1 s of bad readings, and recovery after 1 s of good ones.

    Bad = any axis voltage outside 0.5-2.8 V, or total |g| outside 0.3-3.0 g.
    """

    def __init__(self):
        self.ok = True
        self.problem = ""
        self._bad_since = None
        self._good_since = None

    @staticmethod
    def check(volts, g):
        """Returns a description of the problem, or "" if the reading looks fine."""
        for name, v in zip("XYZ", volts):
            if v < config.MIN_AXIS_V or v > config.MAX_AXIS_V:
                return (f"The {name} wire reads {v:.2f} V. "
                        "Check that the sensor is plugged in properly.")
        total = magnitude(g)
        if total < config.MIN_G_TOTAL or total > config.MAX_G_TOTAL:
            return f"Readings look wrong ({total:.2f} g). Check the sensor wiring and 3V3 power."
        return ""

    def update(self, volts, g, now_ms):
        """Feed one sample. Returns "fault", "recovered" or None."""
        problem = self.check(volts, g)
        if problem:
            self._good_since = None
            if self._bad_since is None:
                self._bad_since = now_ms
            if self.ok and now_ms - self._bad_since >= config.FAULT_AFTER_MS:
                self.ok = False
                self.problem = problem
                return "fault"
        else:
            self._bad_since = None
            if self._good_since is None:
                self._good_since = now_ms
            if not self.ok and now_ms - self._good_since >= config.RECOVER_AFTER_MS:
                self.ok = True
                self.problem = ""
                return "recovered"
        return None


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


@dataclass
class Calibration:
    down: tuple   # gravity vector at the start position
    up: tuple     # gravity vector at the end position
    span_deg: float


def average_if_still(samples):
    """Average a list of vectors. Returns (average, None) if the arm was still,
    otherwise (None, reason)."""
    if not samples:
        return None, "no readings were received. Check the sensor connection."
    for axis in range(3):
        values = [s[axis] for s in samples]
        if max(values) - min(values) > config.MAX_WOBBLE_G:
            return None, "you moved while it was measuring. Hold still and try again."
    n = len(samples)
    avg = tuple(sum(s[axis] for s in samples) / n for axis in range(3))
    return avg, None


def make_calibration(down_samples, up_samples):
    """Build a Calibration from the samples of both positions.

    Returns (Calibration, None) on success or (None, reason) on failure.
    """
    down, why = average_if_still(down_samples)
    if why:
        return None, "start position: " + why
    up, why = average_if_still(up_samples)
    if why:
        return None, "end position: " + why
    span = angle_between(down, up)
    if span < config.MIN_RANGE_DEG:
        return None, (f"the arm only moved {round(span)} degrees (needs at least "
                      f"{int(config.MIN_RANGE_DEG)}). Check the sensor is strapped firmly "
                      "and move all the way.")
    return Calibration(down, up, span), None


class Calibrator:
    """Step-by-step calibration: wait, measure start, wait, measure end.

    Call feed() with every raw sample. It returns a list of events:
      ("step", name)       name is "start_wait", "start_measure", "end_wait", "end_measure"
      ("done", Calibration)
      ("failed", reason)
    """

    STEPS = ["start_wait", "start_measure", "end_wait", "end_measure"]

    def __init__(self, now_ms):
        self.step = "start_wait"
        self.step_start = now_ms
        self.down_samples = []
        self.up_samples = []
        self.finished = False
        self._announced = False

    def seconds_left(self, now_ms):
        total = config.CAL_WAIT_MS if self.step.endswith("wait") else config.CAL_MEASURE_MS
        return max(0.0, (total - (now_ms - self.step_start)) / 1000.0)

    def _go(self, step, now_ms):
        self.step = step
        self.step_start = now_ms
        return [("step", step)]

    def feed(self, raw_g, now_ms):
        if self.finished:
            return []
        events = []
        if not self._announced:
            self._announced = True
            events.append(("step", self.step))
        if now_ms < self.step_start:
            # The ESP32 restarted and its clock went back to 0. Restart this step's
            # timer instead of waiting (possibly minutes) for the clock to catch up.
            self.step_start = now_ms
        elapsed = now_ms - self.step_start

        if self.step == "start_wait":
            if elapsed >= config.CAL_WAIT_MS:
                events += self._go("start_measure", now_ms)
        elif self.step == "start_measure":
            self.down_samples.append(raw_g)
            if elapsed >= config.CAL_MEASURE_MS:
                _, why = average_if_still(self.down_samples)
                if why:
                    self.finished = True
                    return events + [("failed", "start position: " + why)]
                events += self._go("end_wait", now_ms)
        elif self.step == "end_wait":
            if elapsed >= config.CAL_WAIT_MS:
                events += self._go("end_measure", now_ms)
        elif self.step == "end_measure":
            self.up_samples.append(raw_g)
            if elapsed >= config.CAL_MEASURE_MS:
                self.finished = True
                cal, why = make_calibration(self.down_samples, self.up_samples)
                events.append(("failed", why) if why else ("done", cal))
        return events


# ---------------------------------------------------------------------------
# Rep counting state machine
# ---------------------------------------------------------------------------

WAIT_DOWN, READY, CURLING_UP, AT_TOP, LOWERING = (
    "WAIT_DOWN", "READY", "CURLING_UP", "AT_TOP", "LOWERING")

PHASE_LABELS = {
    WAIT_DOWN: "Go to the start position",
    READY: "Ready",
    CURLING_UP: "Lifting",
    AT_TOP: "At the top",
    LOWERING: "Lowering",
}


@dataclass
class RepInfo:
    number: int
    duration_s: float
    rom_deg: float        # range of motion actually reached in this rep
    momentum: bool
    momentum_reason: str = ""


@dataclass
class SetStats:
    full_reps: int = 0
    missed_top: int = 0
    not_extended: int = 0
    timeouts: int = 0
    momentum_warnings: int = 0
    reps: list = field(default_factory=list)   # list of RepInfo

    @property
    def avg_rep_s(self):
        return sum(r.duration_s for r in self.reps) / len(self.reps) if self.reps else 0.0

    @property
    def avg_rom_deg(self):
        return sum(r.rom_deg for r in self.reps) / len(self.reps) if self.reps else 0.0


class RepCounter:
    """Counts full reps from gravity readings.

    update(raw_g, now_ms) - feed one raw sample (applies EMA smoothing)
    step(progress, now_ms, g_mag) - feed a progress value directly (used by tests)

    Both return a list of events:
      ("rep", RepInfo)          a full rep was counted
      ("missed_top", None)      rep abandoned before reaching the top
      ("not_extended", None)    next rep started without returning to the start
      ("timeout", None)         rep took too long and was dropped
      ("momentum", reason)      too much swinging in this rep (said once per rep)
    """

    def __init__(self, calibration, thresholds=None):
        self.cal = calibration
        self.t = dict(config.DEFAULT_THRESHOLDS)
        if thresholds:
            self.t.update(thresholds)
        self.g = calibration.down      # smoothed gravity vector
        self.reset()

    def reset(self):
        """Start a new set."""
        self.phase = WAIT_DOWN
        self.stats = SetStats()
        self.progress = 0.0
        self.rep_start = 0
        self.top_time = None
        self.peak_progress = 0.0
        self.rep_momentum = ""
        self.left_bottom_time = 0   # last time the arm was at the bottom (for lift speed)

    def pause(self):
        """Forget any half-finished rep (used on sensor faults / disconnects)."""
        self.phase = WAIT_DOWN

    def resync(self, raw_g):
        """Jump the filter to the current reading (after a fault clears)."""
        self.g = tuple(raw_g)

    @property
    def angle_deg(self):
        return self.progress / 100.0 * self.cal.span_deg

    def update(self, raw_g, now_ms):
        a = config.FILTER_ALPHA
        self.g = tuple(self.g[i] + a * (raw_g[i] - self.g[i]) for i in range(3))
        progress = angle_between(self.g, self.cal.down) / self.cal.span_deg * 100.0
        return self.step(progress, now_ms, magnitude(raw_g))

    def _start_rep(self, now_ms):
        self.rep_start = now_ms
        self.top_time = None
        self.peak_progress = self.progress
        self.rep_momentum = ""

    def step(self, progress, now_ms, g_mag=1.0):
        t = self.t
        self.progress = progress
        events = []

        in_rep = self.phase in (CURLING_UP, AT_TOP, LOWERING)
        if in_rep:
            self.peak_progress = max(self.peak_progress, progress)

        # A rep that never finishes (e.g. the person stopped halfway) is dropped
        if in_rep and now_ms - self.rep_start > t["rep_timeout_ms"]:
            self.stats.timeouts += 1
            self.phase = WAIT_DOWN
            return [("timeout", None)]

        # Momentum check 1: big linear acceleration on top of gravity while lifting
        if self.phase == CURLING_UP and not self.rep_momentum:
            if abs(g_mag - 1.0) > t["momentum_g_dev"]:
                self.rep_momentum = f"jerky movement ({g_mag:.2f} g)"
                self.stats.momentum_warnings += 1
                events.append(("momentum", self.rep_momentum))

        if self.phase == WAIT_DOWN:
            if progress < t["bottom_pct"]:
                self.phase = READY

        elif self.phase == READY:            # at the start, waiting for a rep
            if progress < t["bottom_pct"]:
                self.left_bottom_time = now_ms
            if progress > t["start_pct"]:
                self.phase = CURLING_UP
                self._start_rep(now_ms)

        elif self.phase == CURLING_UP:       # on the way up
            if progress >= t["top_pct"]:
                self.phase = AT_TOP
                self.top_time = now_ms
                # Momentum check 2: lifting phase much faster than normal.
                # Timed from when the arm left the bottom to when it reached the top.
                lift_s = (now_ms - self.left_bottom_time) / 1000.0
                if lift_s < t["min_lift_s"] and not self.rep_momentum:
                    self.rep_momentum = f"lifted too fast ({lift_s:.2f} s)"
                    self.stats.momentum_warnings += 1
                    events.append(("momentum", self.rep_momentum))
            elif progress < t["bottom_pct"]:
                # went back down without reaching the top. Only counts as a
                # "missed top" if it got reasonably far (above LEFT_TOP), so that
                # small twitches aren't reported.
                if self.peak_progress >= t["left_top_pct"]:
                    self.stats.missed_top += 1
                    events.append(("missed_top", None))
                self.phase = READY

        elif self.phase == AT_TOP:           # reached the top
            if progress < t["left_top_pct"]:
                self.phase = LOWERING

        elif self.phase == LOWERING:         # on the way down
            if progress < t["bottom_pct"]:
                dur = now_ms - self.rep_start
                self.phase = READY
                if dur >= t["min_rep_ms"]:   # faster than this is noise
                    self.stats.full_reps += 1
                    rep = RepInfo(
                        number=self.stats.full_reps,
                        duration_s=dur / 1000.0,
                        rom_deg=min(self.peak_progress, 100.0) / 100.0 * self.cal.span_deg,
                        momentum=bool(self.rep_momentum),
                        momentum_reason=self.rep_momentum,
                    )
                    self.stats.reps.append(rep)
                    events.append(("rep", rep))
            elif progress >= t["top_pct"]:
                # curled again without straightening the arm first
                self.stats.not_extended += 1
                events.append(("not_extended", None))
                self.phase = AT_TOP
                self._start_rep(now_ms)
                self.top_time = now_ms

        return events
