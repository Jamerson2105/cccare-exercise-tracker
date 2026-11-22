"""SQLite database: users, calibrations, workouts, sets and reps.

File: pc_app/tracker.db (created automatically).

Tables
  users(id, name)
  calibrations(user_id, exercise, arm, down_x..up_z, span_deg, created_at)
      one row per user + exercise + arm (saving again replaces it)
  workouts(id, user_id, exercise, arm, started_at)
      one "session": all the sets done in one go. The progress charts are per workout.
  sets(id, workout_id, user_id, exercise, arm, set_number, started_at,
       full_reps, target_reps, missed_top, not_extended, momentum_warnings,
       avg_rep_s, avg_rom_deg, duration_s)
  reps(id, set_id, rep_number, duration_s, rom_deg, momentum)
"""

import os
import sqlite3
import sys
from datetime import datetime

from rep_counter import Calibration

# Keep the database next to the .exe when packaged (PyInstaller unpacks the code to a
# temporary folder that is deleted on exit), otherwise next to this file.
if getattr(sys, "frozen", False):
    DATA_DIR = os.path.dirname(sys.executable)
else:
    DATA_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DATA_DIR, "tracker.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS calibrations (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    exercise   TEXT NOT NULL,
    arm        TEXT NOT NULL,
    down_x REAL, down_y REAL, down_z REAL,
    up_x   REAL, up_y   REAL, up_z   REAL,
    span_deg   REAL NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, exercise, arm)
);
CREATE TABLE IF NOT EXISTS workouts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    exercise   TEXT NOT NULL,
    arm        TEXT NOT NULL,
    started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id        INTEGER REFERENCES workouts(id) ON DELETE CASCADE,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    exercise          TEXT NOT NULL,
    arm               TEXT NOT NULL,
    set_number        INTEGER NOT NULL,
    started_at        TEXT NOT NULL,
    full_reps         INTEGER NOT NULL,
    target_reps       INTEGER NOT NULL,
    missed_top        INTEGER NOT NULL,
    not_extended      INTEGER NOT NULL,
    momentum_warnings INTEGER NOT NULL,
    avg_rep_s         REAL NOT NULL,
    avg_rom_deg       REAL NOT NULL,
    duration_s        REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id     INTEGER NOT NULL REFERENCES sets(id) ON DELETE CASCADE,
    rep_number INTEGER NOT NULL,
    duration_s REAL NOT NULL,
    rom_deg    REAL NOT NULL,
    momentum   INTEGER NOT NULL
);
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, path=DB_PATH):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        if not self.users():
            self.add_user("Guest")

    # ----- users -----
    def users(self):
        """List of (id, name), Guest first then alphabetical."""
        rows = self.conn.execute(
            "SELECT id, name FROM users ORDER BY name <> 'Guest', name COLLATE NOCASE").fetchall()
        return [(r["id"], r["name"]) for r in rows]

    def add_user(self, name):
        with self.conn:
            cur = self.conn.execute("INSERT INTO users(name) VALUES (?)", (name,))
        return cur.lastrowid

    def delete_user(self, user_id):
        """Deletes the user with their calibrations and history."""
        with self.conn:
            self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def user_exists(self, name):
        return self.conn.execute(
            "SELECT 1 FROM users WHERE name = ? COLLATE NOCASE", (name,)).fetchone() is not None

    # ----- calibrations -----
    def save_calibration(self, user_id, exercise, arm, cal):
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO calibrations
                   (user_id, exercise, arm, down_x, down_y, down_z, up_x, up_y, up_z, span_deg, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (user_id, exercise, arm, *cal.down, *cal.up, cal.span_deg, now_str()))

    def load_calibration(self, user_id, exercise, arm):
        """Returns (Calibration, created_at) or (None, None)."""
        r = self.conn.execute(
            "SELECT * FROM calibrations WHERE user_id=? AND exercise=? AND arm=?",
            (user_id, exercise, arm)).fetchone()
        if not r:
            return None, None
        cal = Calibration((r["down_x"], r["down_y"], r["down_z"]),
                          (r["up_x"], r["up_y"], r["up_z"]), r["span_deg"])
        return cal, r["created_at"]

    # ----- workouts, sets and reps -----
    def start_workout(self, user_id, exercise, arm):
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO workouts(user_id, exercise, arm, started_at) VALUES (?,?,?,?)",
                (user_id, exercise, arm, now_str()))
        return cur.lastrowid

    def save_set(self, workout_id, user_id, exercise, arm, set_number, started_at,
                 target_reps, stats, duration_s):
        """stats is a rep_counter.SetStats."""
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO sets (workout_id, user_id, exercise, arm, set_number, started_at,
                       full_reps, target_reps, missed_top, not_extended, momentum_warnings,
                       avg_rep_s, avg_rom_deg, duration_s)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (workout_id, user_id, exercise, arm, set_number, started_at,
                 stats.full_reps, target_reps, stats.missed_top, stats.not_extended,
                 stats.momentum_warnings, round(stats.avg_rep_s, 2),
                 round(stats.avg_rom_deg, 1), round(duration_s, 1)))
            set_id = cur.lastrowid
            self.conn.executemany(
                "INSERT INTO reps(set_id, rep_number, duration_s, rom_deg, momentum) VALUES (?,?,?,?,?)",
                [(set_id, r.number, round(r.duration_s, 2), round(r.rom_deg, 1), int(r.momentum))
                 for r in stats.reps])
        return set_id

    def sets_for_user(self, user_id, exercise=None, arm=None):
        q = "SELECT * FROM sets WHERE user_id = ?"
        args = [user_id]
        if exercise:
            q += " AND exercise = ?"
            args.append(exercise)
        if arm:
            q += " AND arm = ?"
            args.append(arm)
        q += " ORDER BY started_at DESC, id DESC"
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    def sessions(self, user_id, exercise, arm):
        """One row per workout: date, total full reps, average ROM, momentum warnings."""
        q = """SELECT w.id, w.started_at,
                      SUM(s.full_reps) AS total_reps,
                      SUM(s.avg_rom_deg * s.full_reps) / NULLIF(SUM(s.full_reps), 0) AS avg_rom,
                      SUM(s.momentum_warnings) AS momentum
               FROM workouts w JOIN sets s ON s.workout_id = w.id
               WHERE w.user_id = ? AND w.exercise = ? AND w.arm = ?
               GROUP BY w.id ORDER BY w.started_at, w.id"""
        return [dict(r) for r in self.conn.execute(q, (user_id, exercise, arm)).fetchall()]

    def clear_history(self, user_id):
        with self.conn:
            self.conn.execute("DELETE FROM sets WHERE user_id = ?", (user_id,))
            self.conn.execute("DELETE FROM workouts WHERE user_id = ?", (user_id,))
