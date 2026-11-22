"""Tests for rep_counter.py using synthetic angle sequences (no hardware needed).

Run from the project folder:   py -m pytest pc_app/tests
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config  # noqa: E402
import rep_counter as rc  # noqa: E402

DT = 20  # ms between samples (50 Hz)

DOWN = (0.0, 0.0, 1.0)
UP = (0.0, 1.0, 0.0)   # 90 degrees away from DOWN
CAL = rc.Calibration(DOWN, UP, 90.0)


def ramp(start, end, seconds):
    """Progress values going linearly from start% to end% over the given time."""
    n = max(1, int(seconds * 1000 / DT))
    return [start + (end - start) * i / n for i in range(1, n + 1)]


def hold(value, seconds):
    return [value] * max(1, int(seconds * 1000 / DT))


def run(counter, progresses, g_mags=None, t0=0):
    """Feed progress values to the counter, returning (all events, end time)."""
    events = []
    t = t0
    for i, p in enumerate(progresses):
        g = g_mags[i] if g_mags else 1.0
        events += counter.step(p, t, g)
        t += DT
    return events, t


def kinds(events):
    return [k for k, _ in events]


def full_rep():
    """A normal 2.4 s rep: up in 1 s, hold, down in 1 s, rest at the bottom."""
    return ramp(0, 100, 1.0) + hold(100, 0.2) + ramp(100, 0, 1.0) + hold(0, 0.2)


# ---------------------------------------------------------------------------
# Rep counting
# ---------------------------------------------------------------------------

def test_full_rep_is_counted():
    c = rc.RepCounter(CAL)
    events, _ = run(c, hold(0, 0.2) + full_rep())
    assert kinds(events) == ["rep"]
    rep = events[0][1]
    assert rep.number == 1
    assert 1.5 < rep.duration_s < 2.5
    assert rep.rom_deg == 90.0
    assert not rep.momentum
    assert c.stats.full_reps == 1


def test_three_full_reps():
    c = rc.RepCounter(CAL)
    events, _ = run(c, hold(0, 0.2) + full_rep() * 3)
    assert kinds(events) == ["rep", "rep", "rep"]
    assert c.stats.full_reps == 3


def test_didnt_reach_top():
    c = rc.RepCounter(CAL)
    # gets to 70% (above LEFT_TOP 60, below TOP 85) and goes back down
    seq = hold(0, 0.2) + ramp(0, 70, 1.0) + ramp(70, 0, 1.0) + hold(0, 0.2)
    events, _ = run(c, seq)
    assert kinds(events) == ["missed_top"]
    assert c.stats.full_reps == 0
    assert c.stats.missed_top == 1


def test_small_twitch_is_not_reported():
    c = rc.RepCounter(CAL)
    seq = hold(0, 0.2) + ramp(0, 40, 0.5) + ramp(40, 0, 0.5) + hold(0, 0.2)
    events, _ = run(c, seq)
    assert events == []


def test_didnt_fully_extend():
    c = rc.RepCounter(CAL)
    # up to the top, down only to 40% (never below BOTTOM 15), up again, then full finish
    seq = (hold(0, 0.2) + ramp(0, 100, 1.0) + ramp(100, 40, 0.8)
           + ramp(40, 100, 0.8) + ramp(100, 0, 1.0) + hold(0, 0.2))
    events, _ = run(c, seq)
    assert kinds(events) == ["not_extended", "rep"]
    assert c.stats.not_extended == 1
    assert c.stats.full_reps == 1


def test_must_start_from_the_bottom():
    c = rc.RepCounter(CAL)
    # set started with the arm already half up: nothing counts until it goes down
    events, _ = run(c, hold(50, 0.5) + ramp(50, 100, 0.5) + ramp(100, 50, 0.5))
    assert events == []
    assert c.phase == rc.WAIT_DOWN


def test_too_fast_lift_is_flagged_as_momentum_but_counted():
    c = rc.RepCounter(CAL)
    seq = hold(0, 0.2) + ramp(0, 100, 0.25) + hold(100, 0.2) + ramp(100, 0, 1.0) + hold(0, 0.2)
    events, _ = run(c, seq)
    assert kinds(events) == ["momentum", "rep"]
    assert "too fast" in events[0][1]
    assert events[1][1].momentum
    assert c.stats.full_reps == 1
    assert c.stats.momentum_warnings == 1


def test_acceleration_spike_is_flagged_once_per_rep():
    c = rc.RepCounter(CAL)
    seq = hold(0, 0.2) + full_rep()
    # several samples of 1.8 g during the lifting phase
    g = [1.0] * len(seq)
    for i in range(20, 35):
        g[i] = 1.8
    events, _ = run(c, seq, g)
    assert kinds(events) == ["momentum", "rep"]
    assert "jerky" in events[0][1]
    assert c.stats.momentum_warnings == 1


def test_timeout_drops_a_stalled_rep():
    c = rc.RepCounter(CAL)
    timeout_s = config.DEFAULT_THRESHOLDS["rep_timeout_ms"] / 1000
    seq = hold(0, 0.2) + ramp(0, 50, 0.5) + hold(50, timeout_s + 1) + ramp(50, 0, 0.5)
    events, _ = run(c, seq)
    assert kinds(events) == ["timeout"]
    assert c.stats.full_reps == 0
    assert c.stats.timeouts == 1


def test_noise_faster_than_min_rep_is_ignored():
    c = rc.RepCounter(CAL)
    seq = hold(0, 0.2) + ramp(0, 100, 0.1) + ramp(100, 0, 0.1) + hold(0, 0.2)
    events, _ = run(c, seq)
    assert "rep" not in kinds(events)


def test_update_with_real_vectors():
    """Feed actual gravity vectors rotating from DOWN to UP and back."""
    c = rc.RepCounter(CAL)
    events = []
    t = 0
    angles = [0] * 20 + [i * 90 / 50 for i in range(51)] + [90] * 20 + \
             [90 - i * 90 / 50 for i in range(51)] + [0] * 40
    for a in angles:
        r = math.radians(a)
        events += c.update((0.0, math.sin(r), math.cos(r)), t)
        t += DT
    assert kinds(events) == ["rep"]


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def test_calibration_ok():
    down = [DOWN] * 50
    up = [(0.0, math.sin(math.radians(120)), math.cos(math.radians(120)))] * 50
    cal, why = rc.make_calibration(down, up)
    assert why is None
    assert abs(cal.span_deg - 120) < 0.01


def test_calibration_range_too_small():
    down = [DOWN] * 50
    up = [(0.0, math.sin(math.radians(20)), math.cos(math.radians(20)))] * 50
    cal, why = rc.make_calibration(down, up)
    assert cal is None
    assert "only moved 20 degrees" in why


def test_calibration_wobble():
    down = [DOWN] * 25 + [(0.3, 0.0, 0.95)] * 25    # x moves 0.3 g > 0.25 g
    up = [UP] * 50
    cal, why = rc.make_calibration(down, up)
    assert cal is None
    assert "moved" in why and "start position" in why


def test_calibrator_steps_and_success():
    cal_obj = rc.Calibrator(0)
    events = []
    t = 0
    total = 2 * (config.CAL_WAIT_MS + config.CAL_MEASURE_MS) + 200
    while t <= total and not cal_obj.finished:
        in_end = cal_obj.step.startswith("end")
        events += cal_obj.feed(UP if in_end else DOWN, t)
        t += DT
    steps = [v for k, v in events if k == "step"]
    assert steps == ["start_wait", "start_measure", "end_wait", "end_measure"]
    assert events[-1][0] == "done"
    assert abs(events[-1][1].span_deg - 90) < 0.01


def test_calibrator_survives_esp32_clock_reset():
    # Calibration started late in the ESP32's uptime, then the board rebooted
    # (clock back to ~0). It must still finish in normal time, not hang.
    cal_obj = rc.Calibrator(600000)
    cal_obj.feed(DOWN, 600000)
    events = []
    t = 0
    total = 2 * (config.CAL_WAIT_MS + config.CAL_MEASURE_MS) + 400
    while t <= total and not cal_obj.finished:
        events += cal_obj.feed(UP if cal_obj.step.startswith("end") else DOWN, t)
        t += DT
    assert cal_obj.finished
    assert events[-1][0] == "done"


def test_calibrator_fails_on_wobble():
    cal_obj = rc.Calibrator(0)
    events = []
    t = 0
    while t < 10000 and not cal_obj.finished:
        shake = (0.4 if (t // DT) % 2 else 0.0, 0.0, 1.0)
        events += cal_obj.feed(shake, t)
        t += DT
    assert events[-1][0] == "failed"
    assert "moved" in events[-1][1]


# ---------------------------------------------------------------------------
# Sensor health
# ---------------------------------------------------------------------------

def test_sensor_fault_and_recovery():
    h = rc.SensorHealth()
    good_v, good_g = (1.65, 1.65, 1.98), (0, 0, 1)
    bad_v, bad_g = (0.05, 1.65, 1.98), (-4.8, 0, 1)
    results = []
    t = 0
    for _ in range(30):              # 0.6 s bad: not a fault yet
        results.append(h.update(bad_v, bad_g, t)); t += DT
    assert h.ok
    for _ in range(30):              # past 1 s bad: fault
        results.append(h.update(bad_v, bad_g, t)); t += DT
    assert not h.ok and "X wire" in h.problem
    for _ in range(60):              # 1.2 s good: recovered
        results.append(h.update(good_v, good_g, t)); t += DT
    assert h.ok
    assert [r for r in results if r] == ["fault", "recovered"]
