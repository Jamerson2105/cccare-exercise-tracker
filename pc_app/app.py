"""CCCARE Exercise Tracker - PC desktop app.

Run:   py pc_app/app.py            (normal)
       py pc_app/app.py --demo     (start in demo mode, no hardware needed)
       py pc_app/app.py --selftest (scripted demo run that exits when done; for testing)

Threads:
  * the sensor source (USB serial or demo) runs on a background thread and puts
    samples on a queue
  * text-to-speech runs on its own thread (tts.py)
  * everything else, including all rep logic and UI updates, runs on the tkinter
    thread: poll() is called every 30 ms with root.after() and empties the queue.
"""

import csv
import json
import os
import queue
import re
import sqlite3
import sys
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

import config  # noqa: E402
import manual  # noqa: E402
import rep_counter as rc  # noqa: E402
from db import DATA_DIR, Database, DB_PATH, now_str  # noqa: E402
from sensor_source import DemoSource, SerialSource, list_ports  # noqa: E402
from tts import Speaker  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(DATA_DIR, "raw_data")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

POLL_MS = 30
# High-contrast colours (white text on these passes WCAG AA)
RED, GREEN, GREY, BLUE = "#b91c1c", "#15803d", "#4b5563", "#1d4ed8"
INK = "#111827"
TILE_OFF, TILE_OFF_FG = "#ffffff", "#111827"


class Fonts:
    """All fonts in the app, so one click can make every piece of text bigger.

    get(size, weight) returns a shared tkinter Font at `size` x the current scale.
    """

    def __init__(self, scale=1.0):
        self.scale = scale
        self.cache = {}

    def get(self, size, weight="normal", slant="roman"):
        key = (size, weight, slant)
        if key not in self.cache:
            self.cache[key] = tkfont.Font(family="Segoe UI", size=round(size * self.scale),
                                          weight=weight, slant=slant)
        return self.cache[key]

    def set_scale(self, scale):
        self.scale = scale
        for (size, _, _), f in self.cache.items():
            f.configure(size=round(size * scale))
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(size=round(13 * scale))
            except tk.TclError:
                pass


def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(settings, f)
    except OSError:
        pass    # not important enough to bother the user

CAL_STEP_TEXT = {
    "start_wait": "Hold the START position still: {start}.",
    "start_measure": "Measuring the start position... hold still.",
    "end_wait": "Now hold the END position still: {end}.",
    "end_measure": "Measuring the end position... hold still.",
}


class TrackerApp:
    def __init__(self, root, demo=False, db_path=DB_PATH):
        self.root = root
        root.title("CCCARE Exercise Tracker")
        root.geometry("1100x760")
        root.minsize(960, 680)
        try:
            root.state("zoomed")      # open maximized so everything fits
        except tk.TclError:
            pass

        try:
            self.db = Database(db_path)
        except sqlite3.Error as e:
            messagebox.showerror("Database error", f"Couldn't open the database:\n{e}")
            raise
        self.speaker = Speaker()
        self.settings = load_settings()
        self.fonts = Fonts(1.0)
        self.speaker.muted = bool(self.settings.get("muted", False))

        # ----- sensor connection -----
        self.q = queue.Queue()
        self.source = None
        self.last_sample_pc = 0.0      # PC time of the last sample (for "disconnected")
        self.last_ms = 0               # ESP32 millis of the last sample
        self.data_ok = False
        self.health = rc.SensorHealth()
        self.raw_g = (0.0, 0.0, 1.0)

        # ----- selection and calibration -----
        self.user_id = None
        self.sel_user_id = None
        self.users = []
        self.user_var = tk.StringVar()      # name of the chosen person
        self.calibration = None
        self.counter = None
        self.calibrator = None

        # ----- workout state -----
        self.in_set = False
        self.set_started_pc = 0.0
        self.set_started_str = ""
        self.workout_id = None
        self.completed_sets = 0
        self.workout_sets = []        # summary rows for this workout
        self.resting = False
        self.rest_left = 0
        self.rest_job = None

        # ----- raw data recording -----
        self.raw_file = None
        self.raw_writer = None

        self._build_ui()
        self._load_users()
        self._fit_to_screen()
        self.demo_var.set(demo)
        self.start_source()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(POLL_MS, self.poll)

    # =====================================================================
    # UI layout
    # =====================================================================
    def _build_ui(self):
        F = self.fonts.get
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        self.fonts.set_scale(self.fonts.scale)      # applies to Tk's default fonts too
        style.configure(".", font=F(13))
        style.configure("TCheckbutton", font=F(13))
        self.px = manual.screen_scale(self.root)     # screen pixels per normal pixel
        style.configure("Treeview", font=F(13), rowheight=int(32 * self.fonts.scale * self.px))
        style.configure("Treeview.Heading", font=F(12, "bold"))
        style.configure("TNotebook.Tab", font=F(15, "bold"), padding=(22, 8))
        style.configure("Card.TLabelframe.Label", font=F(14, "bold"))
        self.root.option_add("*TCombobox*Listbox.font", F(15))   # big drop-down lists

        # --- top bar: big status box + a few switches; everything else is in Settings ---
        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill="x")
        self.conn_var = tk.StringVar(value="Not connected")
        self.conn_chip = tk.Label(top, textvariable=self.conn_var, bg=GREY, fg="white",
                                  font=F(14, "bold"), padx=16, pady=6)
        self.conn_chip.pack(side="left")
        self._big_button(top, "⟳  Reconnect", GREY, self.start_source, size=12).pack(side="left", padx=8)
        self.mute_var = tk.BooleanVar(value=self.speaker.muted)
        ttk.Checkbutton(top, text="Mute voice", variable=self.mute_var,
                        command=self._toggle_mute).pack(side="left", padx=8)

        self.adv_btn = ttk.Button(top, text="⚙  Settings ▸", command=self._toggle_advanced)
        self.adv_btn.pack(side="right")
        # Settings (hidden until asked for)
        self.adv = ttk.Frame(self.root, padding=(10, 0, 10, 6))
        row1 = ttk.Frame(self.adv)
        row1.pack(fill="x")
        self.show_angle_var = tk.BooleanVar(value=bool(self.settings.get("show_angle", False)))
        ttk.Checkbutton(row1, text="Show arm angle during exercise", variable=self.show_angle_var,
                        command=self._toggle_angle).pack(side="left")
        self.demo_var = tk.BooleanVar()
        ttk.Checkbutton(row1, text="Demo mode (no sensor)", variable=self.demo_var,
                        command=self.start_source).pack(side="left", padx=16)
        row2 = ttk.Frame(self.adv)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Label(row2, text="USB port:").pack(side="left")
        self.port_var = tk.StringVar(value="Auto")
        self.port_box = ttk.Combobox(row2, textvariable=self.port_var, width=10, state="readonly",
                                     postcommand=self._refresh_ports, font=F(13))
        self.port_box.pack(side="left", padx=4)
        ttk.Label(row2, text="(pick a port, then press Reconnect)", foreground=GREY).pack(side="left")
        self.record_var = tk.BooleanVar()
        ttk.Checkbutton(row2, text="Record raw data (for tuning)", variable=self.record_var,
                        command=self.toggle_recording).pack(side="left", padx=16)

        # Keyboard shortcuts (ignored while typing in a box)
        self.root.bind_all("<space>", self._key_start_stop)
        self.root.bind_all("<Key-c>", self._key_calibrate)
        self.root.bind_all("<Key-C>", self._key_calibrate)

        # --- red fault banner (hidden unless there is a problem) ---
        self.banner = tk.Label(self.root, text="", bg=RED, fg="white",
                               font=F(15, "bold"), pady=8)
        self.banner.bind("<Configure>", lambda e: self.banner.config(wraplength=max(300, e.width - 40)))

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._build_workout_tab()
        self._build_history_tab()
        self._build_progress_tab()
        self._build_guide_tab()

    # ---------------------------------------------------------------------
    # Workout tab: three pages, one after the other
    #   1 Setup      who, exercise, arm and the plan, all on one page
    #   2 Calibrate  where the sensor goes + calibration
    #   3 Exercise   the rep counter
    # ---------------------------------------------------------------------
    PAGES = ("setup", "calibrate", "exercise")

    def _build_workout_tab(self):
        F = self.fonts.get
        outer = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(outer, text="  Workout  ")
        self.workout_tab = outer

        # Step strip: which page you are on
        strip = ttk.Frame(outer)
        strip.pack(fill="x", pady=(0, 10))
        self.step_labels = []
        for i, text in enumerate(["1  Setup", "2  Calibrate", "3  Exercise"]):
            lbl = tk.Label(strip, text=text, font=F(14, "bold"), padx=12, pady=8)
            lbl.grid(row=0, column=i * 2, sticky="ew")
            strip.columnconfigure(i * 2, weight=1)
            if i < 2:
                tk.Label(strip, text="→", fg=GREY, font=F(16)).grid(row=0, column=i * 2 + 1)
            self.step_labels.append(lbl)

        self.choice_var = tk.StringVar()       # "Guest  ·  Bicep curl  ·  Right arm"
        self.place_var = tk.StringVar()
        self.msg_var = tk.StringVar(value="")
        self.back_btns = []
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        self.pages = {"setup": self._build_setup_page(body),
                      "calibrate": self._build_calibrate_page(body),
                      "exercise": self._build_exercise_page(body)}
        self.page = None
        self.go_page("setup")

    def _build_setup_page(self, parent):
        page = ttk.Frame(parent)
        page.columnconfigure(0, weight=1, uniform="setup")
        page.columnconfigure(1, weight=1, uniform="setup")

        who = ttk.LabelFrame(page, text="Who is exercising?", padding=10, style="Card.TLabelframe")
        who.grid(row=0, column=0, sticky="nsew", padx=(0, 7), pady=(0, 10))
        self.user_grid = ttk.Frame(who)          # one big button per person (see _load_users)
        self.user_grid.pack(fill="x")
        self.user_tiles = {}
        row = ttk.Frame(who)
        row.pack(fill="x", pady=(10, 0))
        self._big_button(row, "+ Add person", GREY, self.add_user, size=12).pack(side="left")
        self._big_button(row, "Delete person", GREY, self.delete_user, size=12).pack(side="left", padx=8)

        box = ttk.LabelFrame(page, text="Which exercise?", padding=10, style="Card.TLabelframe")
        box.grid(row=0, column=1, sticky="nsew", padx=(7, 0), pady=(0, 10))
        self.ex_var = tk.StringVar(value=list(config.EXERCISES)[0])
        self.ex_tiles = {}
        for i, name in enumerate(config.EXERCISES):
            short = name.replace("Dumbbell ", "").capitalize()
            b = self._tile(box, short, lambda n=name: self._pick_exercise(n))
            b.grid(row=i // 2, column=i % 2, sticky="nsew", padx=3, pady=3)
            self.ex_tiles[name] = b
        box.columnconfigure(0, weight=1, uniform="ex")
        box.columnconfigure(1, weight=1, uniform="ex")

        box = ttk.LabelFrame(page, text="Which arm?", padding=10, style="Card.TLabelframe")
        box.grid(row=1, column=0, sticky="nsew", padx=(0, 7), pady=(0, 10))
        self.arm_var = tk.StringVar(value="Right")
        self.arm_tiles = {}
        for i, arm in enumerate(config.ARMS):
            b = self._tile(box, f"{arm} arm", lambda a=arm: self._pick_arm(a))
            b.grid(row=0, column=i, sticky="nsew", padx=3, pady=3)
            box.columnconfigure(i, weight=1, uniform="arm")
            self.arm_tiles[arm] = b

        # Workout plan: big - / + buttons instead of small number boxes
        plan = ttk.LabelFrame(page, text="Your plan", padding=10, style="Card.TLabelframe")
        plan.grid(row=1, column=1, sticky="nsew", padx=(7, 0), pady=(0, 10))
        inner = ttk.Frame(plan)
        inner.pack()
        self.sets_var = tk.IntVar(value=config.DEFAULT_SETS)
        self.reps_var = tk.IntVar(value=config.DEFAULT_REPS)
        self.rest_var = tk.IntVar(value=config.DEFAULT_REST_S)
        self._stepper(inner, "Sets", self.sets_var, 1, 20, 1, "").grid(row=0, column=0, padx=14)
        self._stepper(inner, "Reps per set", self.reps_var, 1, 50, 1, "").grid(row=0, column=1, padx=14)
        self._stepper(inner, "Rest (seconds)", self.rest_var, config.MIN_REST_S, config.MAX_REST_S, 15,
                      "").grid(row=0, column=2, padx=14)

        nav = ttk.Frame(page)
        nav.grid(row=2, column=0, columnspan=2, sticky="ew")
        self._big_button(nav, "Next  →", GREEN, lambda: self.go_page("calibrate"), size=18).pack(side="right")
        return page

    def _build_calibrate_page(self, parent):
        F = self.fonts.get
        page = ttk.Frame(parent)
        nav = ttk.Frame(page)                # packed first so it always stays visible
        nav.pack(fill="x", side="bottom")
        tk.Label(page, textvariable=self.choice_var, font=F(15, "bold"), fg=GREY).pack()
        tk.Label(page, textvariable=self.place_var, fg=manual.SENSOR, font=F(18, "bold")).pack(pady=(2, 6))
        self.poses = manual.PosePair(page, self.fonts, size=self._pose_size())
        self.poses.pack()
        self.cal_var = tk.StringVar()
        self.cal_label = tk.Label(page, textvariable=self.cal_var, font=F(15, "bold"))
        self.cal_label.pack(pady=(8, 0))
        msg = tk.Label(page, textvariable=self.msg_var, font=F(17, "bold"), fg=BLUE, justify="center")
        msg.pack(fill="x", pady=4)
        msg.bind("<Configure>", lambda e, m=msg: m.config(wraplength=max(300, e.width - 20)))
        tk.Label(page, text="Only needed the first time for each exercise and arm.",
                 fg=GREY, font=F(12)).pack()

        back = self._big_button(nav, "←  Back", GREY, lambda: self.go_page("setup"), size=16)
        back.pack(side="left")
        self.back_btns.append(back)
        self.cal_next_btn = self._big_button(nav, "Next  →", GREEN, self._next_from_calibrate, size=18)
        self.cal_next_btn.pack(side="right")
        middle = ttk.Frame(nav)
        middle.pack(side="top")
        self.cal_btn = self._big_button(middle, "Calibrate", BLUE, self._calibrate_pressed, size=18)
        self.cal_btn.pack(side="left", padx=8)
        self._big_button(middle, "?  How do I do this?", GREY,
                         lambda: self.show_guide(self.ex_var.get()), size=13).pack(side="left", padx=8)
        return page

    def _build_exercise_page(self, parent):
        F = self.fonts.get
        page = ttk.Frame(parent)
        nav = ttk.Frame(page)                # packed first so it always stays visible
        nav.pack(fill="x", side="bottom")
        mid = ttk.Frame(page)
        mid.pack(fill="both", expand=True)
        mid.columnconfigure(0, weight=1)
        tk.Label(mid, textvariable=self.choice_var, font=F(15, "bold"), fg=GREY).grid(row=0, column=0)

        self.set_var = tk.StringVar(value="Set 1 of 3")
        tk.Label(mid, textvariable=self.set_var, font=F(22, "bold"), fg=INK).grid(row=1, column=0, pady=(6, 0))
        self.reps_big = tk.Label(mid, text="0 / 10", font=F(72, "bold"), fg=INK, padx=20)
        self.reps_big.grid(row=2, column=0)

        self.progress = ttk.Progressbar(mid, maximum=100, length=500)
        self.progress.grid(row=3, column=0, sticky="ew", padx=80, ipady=6)

        stats = ttk.Frame(mid)
        stats.grid(row=4, column=0, pady=10)
        self.angle_var = tk.StringVar(value="– °")
        self.phase_var = tk.StringVar(value="–")
        self.inc_var = tk.StringVar(value="0")
        self.mom_var = tk.StringVar(value="0")
        self.last_var = tk.StringVar(value="–")
        for i, (title, var) in enumerate([("Right now", self.phase_var), ("Incomplete reps", self.inc_var),
                                          ("Swinging", self.mom_var), ("Arm angle", self.angle_var)]):
            f = ttk.Frame(stats, padding=(18, 0))
            f.grid(row=0, column=i)
            tk.Label(f, textvariable=var, font=F(20, "bold"), fg=INK).pack()
            tk.Label(f, text=title, fg=GREY, font=F(13)).pack()
        self.angle_box = f              # the last one; hidden unless turned on in Settings
        self._apply_show_angle()

        msg = tk.Label(mid, textvariable=self.msg_var, font=F(17, "bold"), fg=BLUE, justify="center")
        msg.grid(row=5, column=0, pady=8, sticky="ew")
        msg.bind("<Configure>", lambda e, m=msg: m.config(wraplength=max(300, e.width - 20)))

        btns = ttk.Frame(mid)
        btns.grid(row=6, column=0, pady=6)
        self.start_btn = self._big_button(btns, "▶  Start Set", GREEN, self.start_set, size=20)
        self.start_btn.grid(row=0, column=0, padx=10)
        self.stop_btn = self._big_button(btns, "■  Stop Set", RED, self.stop_set, size=20)
        self.stop_btn.grid(row=0, column=1, padx=10)
        self.why_var = tk.StringVar(value="")
        self.why_label = tk.Label(mid, textvariable=self.why_var, fg=GREY, font=F(13, "normal", "italic"))
        self.why_label.grid(row=7, column=0)
        self.why_label.grid_remove()


        back = self._big_button(nav, "←  Back", GREY, lambda: self.go_page("calibrate"), size=16)
        back.pack(side="left")
        self.rest_frame = ttk.Frame(nav)
        self.rest_frame.pack(side="top")
        self.rest_label = tk.Label(self.rest_frame, text="", font=F(32, "bold"), fg=GREEN)
        self.rest_label.pack(side="left")
        self.skip_btn = self._big_button(self.rest_frame, "Skip Rest", GREY, self.skip_rest, size=14)
        self.back_btns.append(back)
        return page

    def go_page(self, name):
        """Show one page of the Workout tab (only when nothing is running)."""
        if name == self.page:
            return
        if self.page is not None and (self.in_set or self.calibrator is not None):
            return
        if self.page is not None:
            self.pages[self.page].pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        self.page = name
        self._update_steps()
        if name == "calibrate":
            self.msg_var.set("Press Next to start exercising." if self.calibration
                             else "Strap on the sensor, then press Calibrate.")
        elif name == "exercise":
            self.msg_var.set(f"Ready. Get into the start position ({config.EXERCISES[self.ex_var.get()]['start']}),"
                             " then press Start Set.")

    def _next_from_calibrate(self):
        if self.calibration and not self.calibrator:
            self.go_page("exercise")

    def _pose_size(self):
        """Size of the START/END pictures on the Calibrate page."""
        return 0.85 * self.fonts.scale

    def _build_guide_tab(self):
        self.guide = manual.GuideView(self.tabs, self.fonts)
        self.tabs.add(self.guide, text="  Guide  ")
        self.guide.show("Getting started")

    def show_guide(self, topic):
        """Open the Guide tab at a topic (an exercise name, or e.g. "Getting started")."""
        self.tabs.select(self.guide)
        self.guide.show(topic)

    # ----- small UI helpers -----
    def _big_button(self, parent, text, color, command, size=16):
        """A large, coloured button (plain tk so the colour shows on Windows)."""
        b = tk.Button(parent, text=text, command=command, bg=color, fg="white",
                      activebackground=color, activeforeground="white",
                      disabledforeground="#f3f4f6", font=self.fonts.get(size, "bold"),
                      relief="flat", padx=24, pady=10, cursor="hand2", takefocus=0)
        b.color = color          # remembered so the colour comes back when re-enabled
        return b

    def _tile(self, parent, text, command):
        """A big choice button. Selected = blue, not selected = white with a border."""
        return tk.Button(parent, text=text, command=command, font=self.fonts.get(14, "bold"),
                         bg=TILE_OFF, fg=TILE_OFF_FG, activebackground="#e0e7ff", relief="solid",
                         bd=1, padx=10, pady=8, cursor="hand2", takefocus=0,
                         wraplength=int(190 * self.px * self.fonts.scale))

    def _stepper(self, parent, label, var, lo, hi, step, unit):
        """  Label
             [ − ]  10  [ + ]   big buttons for changing a number"""
        F = self.fonts.get
        f = ttk.Frame(parent)
        tk.Label(f, text=label, font=F(13), fg=GREY).grid(row=0, column=0, columnspan=3)

        def change(d):
            try:
                v = int(var.get())
            except (tk.TclError, ValueError):
                v = lo
            var.set(max(lo, min(hi, v + d)))
            self._refresh_live()

        tk.Button(f, text="−", width=3, font=F(16, "bold"), relief="flat", bg="#e5e7eb", cursor="hand2",
                  takefocus=0, command=lambda: change(-step)).grid(row=1, column=0)
        value = tk.Label(f, font=F(20, "bold"), width=4, fg=INK)
        value.grid(row=1, column=1)
        tk.Button(f, text="+", width=3, font=F(16, "bold"), relief="flat", bg="#e5e7eb", cursor="hand2",
                  takefocus=0, command=lambda: change(step)).grid(row=1, column=2)

        def show(*_):
            try:
                value.config(text=f"{int(var.get())}{unit}")
            except (tk.TclError, ValueError):
                pass
        var.trace_add("write", show)
        show()
        return f

    @staticmethod
    def _set_enabled(button, enabled):
        want = "normal" if enabled else "disabled"
        if button.cget("state") != want:      # only touch the widget when it changes
            button.config(state=want, cursor="hand2" if enabled else "arrow",
                          bg=button.color if enabled else "#c4c9d1")

    def _pick_exercise(self, name):
        self.ex_var.set(name)
        self.on_selection_changed()

    def _pick_arm(self, arm):
        self.arm_var.set(arm)
        self.on_selection_changed()

    def _pick_user(self, uid):
        self.sel_user_id = uid
        self.user_var.set(dict(self.users)[uid])
        self.on_selection_changed()

    def _all_tiles(self):
        return list(self.user_tiles.values()) + list(self.ex_tiles.values()) + list(self.arm_tiles.values())

    def _show_tiles(self):
        """Colour the selected person, exercise and arm, and lock them during a set or calibration."""
        locked = self.in_set or self.calibrator is not None
        for tiles, chosen in ((self.user_tiles, self.user_id), (self.ex_tiles, self.ex_var.get()),
                              (self.arm_tiles, self.arm_var.get())):
            for name, b in tiles.items():
                on = name == chosen
                want = (BLUE if on else TILE_OFF, "white" if on else TILE_OFF_FG,
                        "disabled" if (locked and not on) else "normal")
                if getattr(b, "_look", None) != want:
                    b._look = want
                    b.config(bg=want[0], fg=want[1], state=want[2], activebackground=want[0] if on else "#e0e7ff",
                             activeforeground=want[1], disabledforeground="#9ca3af")

    def _fit_to_screen(self):
        """Shrink text and pictures until every Workout page fits the screen height.

        Big screens keep full size; small laptop screens or projectors get smaller text
        instead of buttons falling off the bottom of the window.
        """
        title_and_taskbar = int(90 * self.px)
        room_for_banner = int(50 * self.px)      # the red fault banner can appear at any time
        available = self.root.winfo_screenheight() - title_and_taskbar - room_for_banner
        current = self.page
        # Measure with a normal one-line message, not whatever long one is showing now
        saved_msg = self.msg_var.get()
        self.msg_var.set("Ready. Press Start Set.")
        scale = 1.0
        while True:
            self._apply_scale(scale)
            need = 0
            for name, page in self.pages.items():
                for other in self.pages.values():
                    other.pack_forget()
                page.pack(fill="both", expand=True)
                self.root.update_idletasks()
                # The window's height also counts the tallest other tab (Guide, charts),
                # so count only the Workout tab plus the bars above it.
                tabs = self.tabs.winfo_children()
                tab_header = self.tabs.winfo_reqheight() - max(w.winfo_reqheight() for w in tabs)
                above = self.root.winfo_reqheight() - self.tabs.winfo_reqheight()
                need = max(need, above + tab_header + self.workout_tab.winfo_reqheight())
            if need <= available or scale <= 0.6:
                break
            scale = round(scale - 0.05, 2)
        for page in self.pages.values():
            page.pack_forget()
        self.pages[current].pack(fill="both", expand=True)
        self.msg_var.set(saved_msg)
        self.fit_info = (scale, need, available)

    def _apply_scale(self, scale):
        self.fonts.set_scale(scale)
        ttk.Style().configure("Treeview", rowheight=int(32 * scale * self.px))
        for b in self._all_tiles():
            b.config(wraplength=int(190 * self.px * scale))
        self.poses.show(self.ex_var.get(), size=self._pose_size())

    def _toggle_mute(self):
        self.speaker.muted = self.mute_var.get()
        self.settings["muted"] = self.mute_var.get()
        save_settings(self.settings)

    def _toggle_advanced(self):
        if self.adv.winfo_ismapped():
            self.adv.pack_forget()
            self.adv_btn.config(text="⚙  Settings ▸")
        else:
            self.adv.pack(fill="x", before=self.tabs)
            self.adv_btn.config(text="⚙  Settings ▾")

    def _toggle_angle(self):
        self.settings["show_angle"] = self.show_angle_var.get()
        save_settings(self.settings)
        self._apply_show_angle()

    def _apply_show_angle(self):
        if self.show_angle_var.get():
            self.angle_box.grid()
        else:
            self.angle_box.grid_remove()

    def _typing(self, event):
        return isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox, tk.Spinbox))

    def _on_workout_page(self, event, page):
        return (not self._typing(event) and self.tabs.index(self.tabs.select()) == 0
                and self.page == page)

    def _key_start_stop(self, event):
        if not self._on_workout_page(event, "exercise"):
            return
        if self.in_set:
            self.stop_set()
        else:
            self.start_set()
        return "break"

    def _key_calibrate(self, event):
        if self._on_workout_page(event, "calibrate") and not self.in_set and not self.calibrator:
            self.start_calibration()

    def flash_reps(self, color):
        """Briefly colour the big rep counter (green = counted, orange = problem)."""
        self.reps_big.config(bg=color)
        self.root.after(350, lambda: self.reps_big.config(bg=self.root.cget("bg")))

    def _update_steps(self):
        """Highlight the current page in the 1-2-3 strip."""
        current = self.PAGES.index(self.page)
        for i, lbl in enumerate(self.step_labels):
            if i < current:
                lbl.config(bg="#dcfce7", fg=GREEN)          # done
            elif i == current:
                lbl.config(bg=BLUE, fg="white")             # do this now
            else:
                lbl.config(bg=self.root.cget("bg"), fg=GREY)

    def _build_history_tab(self):
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="  History  ")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 8))
        self.hist_title = ttk.Label(bar, text="", font=self.fonts.get(15, "bold"))
        self.hist_title.pack(side="left")
        ttk.Label(bar, text="   Exercise:").pack(side="left")
        self.hist_ex = tk.StringVar(value="All")
        cb = ttk.Combobox(bar, textvariable=self.hist_ex, state="readonly", width=24,
                          values=["All"] + list(config.EXERCISES), font=self.fonts.get(13))
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_history())
        ttk.Label(bar, text="Arm:").pack(side="left")
        self.hist_arm = tk.StringVar(value="All")
        cb = ttk.Combobox(bar, textvariable=self.hist_arm, state="readonly", width=7,
                          values=["All"] + list(config.ARMS), font=self.fonts.get(13))
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_history())
        ttk.Button(bar, text="Clear this user's history", command=self.clear_history).pack(side="right")
        ttk.Button(bar, text="Export CSV", command=self.export_csv).pack(side="right", padx=6)

        cols = [("started_at", "Date and time", 170), ("exercise", "Exercise", 200), ("arm", "Arm", 70),
                ("set_number", "Set", 50), ("reps", "Reps", 90), ("missed_top", "Didn't reach top", 140),
                ("not_extended", "Didn't straighten", 150), ("momentum_warnings", "Swinging", 90),
                ("avg_rep_s", "Seconds per rep", 130), ("avg_rom_deg", "Range (°)", 90),
                ("duration_s", "Set time (s)", 110)]
        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True)
        self.hist_tree = ttk.Treeview(frame, columns=[c[0] for c in cols], show="headings")
        for key, title, width in cols:
            self.hist_tree.heading(key, text=title)
            self.hist_tree.column(key, width=width, anchor="center")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb.set)
        self.hist_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.hist_rows = []

    def _build_progress_tab(self):
        tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(tab, text="  Progress  ")
        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        self.prog_title = ttk.Label(bar, text="", font=self.fonts.get(15, "bold"))
        self.prog_title.pack(side="left")
        ttk.Label(bar, text="   Exercise:").pack(side="left")
        self.prog_ex = tk.StringVar(value=list(config.EXERCISES)[0])
        cb = ttk.Combobox(bar, textvariable=self.prog_ex, state="readonly", width=24,
                          values=list(config.EXERCISES), font=self.fonts.get(13))
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_progress())
        ttk.Label(bar, text="Arm:").pack(side="left")
        self.prog_arm = tk.StringVar(value="Right")
        cb = ttk.Combobox(bar, textvariable=self.prog_arm, state="readonly", width=7, values=list(config.ARMS),
                          font=self.fonts.get(13))
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_progress())
        self.compare_var = tk.StringVar(value="")
        ttk.Label(tab, textvariable=self.compare_var, foreground=GREY).pack(anchor="w", pady=(4, 0))

        self.fig = Figure(figsize=(9, 6), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, master=tab)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    # =====================================================================
    # Sensor connection
    # =====================================================================
    def _refresh_ports(self):
        self.port_box["values"] = ["Auto"] + list_ports()

    def start_source(self):
        """(Re)start reading from the ESP32, or from the simulator in demo mode."""
        if self.source:
            self.source.stop()
            # Wait for the old reader to let go of the COM port, or the new one can't open it
            self.source.join(timeout=1.5)
            # Reconnecting reboots the ESP32 (its clock restarts), so a running
            # calibration can't carry on. Stop it so nothing stays locked.
            self._cancel_calibration("Calibration stopped because the sensor was reconnected. "
                                     "Press Calibrate to try again.")
            if self.in_set and self.counter:
                self.counter.pause()
        self.q = queue.Queue()        # drop anything left from the old source
        self.conn_chip.config(bg=GREY)
        self.data_ok = False
        self.last_sample_pc = time.time()
        self.last_ms = 0
        self.health = rc.SensorHealth()
        if self.demo_var.get():
            self.source = DemoSource(self.q)
            if self.in_set:
                self.source.mode = "reps"
        else:
            port = None if self.port_var.get() in ("", "Auto") else self.port_var.get()
            self.source = SerialSource(self.q, port)
            self.conn_var.set("Connecting...")
        self.source.start()

    def _demo_mode(self, mode):
        """Tell the simulated person what to do (no effect with real hardware)."""
        if isinstance(self.source, DemoSource):
            self.source.mode = mode

    def poll(self):
        """Runs every 30 ms on the UI thread: handle new samples and refresh the screen."""
        try:
            for _ in range(500):
                item = self.q.get_nowait()
                if item[0] == "status":
                    self.conn_var.set(item[1])
                else:
                    self.handle_sample(*item[1:])
        except queue.Empty:
            pass

        # No data for more than 1 s = disconnected
        if self.data_ok and time.time() - self.last_sample_pc > config.NO_DATA_TIMEOUT_S:
            self.data_ok = False
            self.on_disconnected()

        self._refresh_live()
        self.root.after(POLL_MS, self.poll)

    def handle_sample(self, ms, x_mv, y_mv, z_mv):
        self.last_sample_pc = time.time()
        if not self.data_ok:
            self.data_ok = True
            self.on_connected()
        if ms < self.last_ms:          # ESP32 restarted: its clock went back to 0
            self.health = rc.SensorHealth()
            if self.counter:
                self.counter.pause()
            self._cancel_calibration("Calibration stopped: the sensor restarted. Press Calibrate to try again.")
        self.last_ms = ms

        volts = (x_mv / 1000.0, y_mv / 1000.0, z_mv / 1000.0)
        g = (rc.mv_to_g(x_mv), rc.mv_to_g(y_mv), rc.mv_to_g(z_mv))
        self.raw_g = g

        event = self.health.update(volts, g, ms)
        if event == "fault":
            self.on_sensor_fault()
        elif event == "recovered":
            self.on_sensor_recovered(g)

        if self.health.ok:
            if self.calibrator:
                for kind, value in self.calibrator.feed(g, ms):
                    self.on_calibration_event(kind, value)
            elif self.counter:
                events = self.counter.update(g, ms)
                if self.in_set:
                    for kind, value in events:
                        self.on_rep_event(kind, value)

        if self.raw_writer:
            c = self.counter
            self.raw_writer.writerow([
                f"{time.time():.3f}", ms, x_mv, y_mv, z_mv,
                f"{g[0]:.3f}", f"{g[1]:.3f}", f"{g[2]:.3f}", f"{rc.magnitude(g):.3f}",
                f"{c.progress:.1f}" if c else "", c.phase if c else "", int(self.in_set),
                self.ex_var.get(), self.arm_var.get()])

    def on_connected(self):
        self.conn_chip.config(bg=GREEN)
        if not self.demo_var.get():
            port = getattr(self.source, "connected_port", None)
            self.conn_var.set(f"Sensor connected ({port})" if port else "Sensor connected")
        self._update_banner()
        if self.counter:
            self.counter.resync(self.raw_g)

    def on_disconnected(self):
        self.conn_chip.config(bg=RED)
        self.conn_var.set("Sensor disconnected")
        self.speaker.say("Sensor disconnected.")
        self._cancel_calibration("Calibration stopped: the sensor disconnected.")
        if self.in_set and self.counter:
            self.counter.pause()
            self.msg_var.set("Sensor disconnected - counting is paused. Check the USB cable.")
        self._update_banner()

    def on_sensor_fault(self):
        self.speaker.say("Sensor problem. Please check the wiring.")
        self._cancel_calibration("Calibration failed: sensor problem.")
        if self.in_set and self.counter:
            self.counter.pause()      # don't count anything half-measured
        self._update_banner()

    def on_sensor_recovered(self, g):
        self.speaker.say("Sensor working again.")
        if self.counter:
            self.counter.resync(g)
        self._update_banner()

    def _update_banner(self):
        problem = ""
        if not self.health.ok:
            problem = "Sensor problem: " + self.health.problem
        elif not self.data_ok and self.source is not None and time.time() - self.last_sample_pc > 2:
            problem = "Sensor disconnected - no data from the tracker."
        if problem:
            if self.in_set or self.calibrator:
                problem += "  Counting is paused."
            self.banner.config(text=problem)
            self.banner.pack(fill="x", before=self.tabs, padx=10, pady=(0, 6))
        else:
            self.banner.pack_forget()

    # =====================================================================
    # Users, exercise, arm
    # =====================================================================
    def _load_users(self, select_id=None):
        try:
            self.users = self.db.users()
        except sqlite3.Error as e:
            messagebox.showerror("Database error", str(e))
            return
        # One big button per person, three to a row
        for b in self.user_tiles.values():
            b.destroy()
        self.user_tiles = {}
        for i, (uid, name) in enumerate(self.users):
            b = self._tile(self.user_grid, name, lambda u=uid: self._pick_user(u))
            b.grid(row=i // 3, column=i % 3, sticky="nsew", padx=3, pady=3)
            self.user_tiles[uid] = b
        for c in range(3):
            self.user_grid.columnconfigure(c, weight=1, uniform="user")
        ids = [uid for uid, _ in self.users]
        if select_id not in ids:
            select_id = self.user_id if self.user_id in ids else ids[0]
        self._pick_user(select_id)
        rows = (len(self.users) + 2) // 3
        if hasattr(self, "fit_info") and rows != getattr(self, "_user_rows", rows):
            self._fit_to_screen()            # a new row of names may not fit any more
        self._user_rows = rows

    def _selected_user_id(self):
        ids = [uid for uid, _ in self.users]
        return self.sel_user_id if self.sel_user_id in ids else ids[0]

    def add_user(self):
        name = simpledialog.askstring("Add user", f"New user name (1-{config.MAX_NAME_LEN} characters):",
                                      parent=self.root)
        if name is None:
            return
        name = re.sub(r"\s+", " ", name).strip()
        if not name:
            messagebox.showwarning("Add user", "Please enter a name.")
        elif len(name) > config.MAX_NAME_LEN:
            messagebox.showwarning("Add user", f"Names can be at most {config.MAX_NAME_LEN} characters.")
        elif self.db.user_exists(name):
            messagebox.showwarning("Add user", f"There is already a user called {name}.")
        else:
            try:
                uid = self.db.add_user(name)
            except sqlite3.Error as e:
                messagebox.showerror("Database error", str(e))
                return
            self._load_users(select_id=uid)
            self.speaker.say(f"Added {name}. Please calibrate.")

    def delete_user(self):
        name = self.user_var.get()
        if name == "Guest":
            messagebox.showinfo("Delete user", "The Guest user can't be deleted.")
            return
        if not messagebox.askyesno("Delete user",
                                   f"Delete {name}?\n\nTheir calibrations and workout history "
                                   "will be deleted too. This can't be undone."):
            return
        self._end_activity()
        try:
            self.db.delete_user(self._selected_user_id())
        except sqlite3.Error as e:
            messagebox.showerror("Database error", str(e))
            return
        self.user_id = None
        self._load_users()

    def _end_activity(self):
        """Stop any running set / calibration / rest and finish the workout."""
        if self.in_set:
            self.end_set(announce=False, allow_rest=False)
        self.calibrator = None
        self._stop_rest()
        self._finish_workout(show_summary=False)

    def on_selection_changed(self):
        """User, exercise or arm changed: stop what's running and load the calibration."""
        new = (self._selected_user_id(), self.ex_var.get(), self.arm_var.get())
        old = getattr(self, "_selection", None)
        if new == old:
            return
        if old is not None:
            self._end_activity()
        self._selection = new
        self.user_id = new[0]
        ex = config.EXERCISES[self.ex_var.get()]
        self.place_var.set(f"Strap the sensor on the {ex['sensor']}")
        self.choice_var.set(f"{self.user_var.get()}   ·   {self.ex_var.get()}   ·   {self.arm_var.get()} arm")
        self.poses.show(self.ex_var.get())
        self.hist_title.config(text=f"History for {self.user_var.get()}")
        self.load_calibration(announce=old is not None)
        self.msg_var.set("")
        self._show_tiles()

    def load_calibration(self, announce=False):
        try:
            cal, created = self.db.load_calibration(self.user_id, self.ex_var.get(), self.arm_var.get())
        except sqlite3.Error as e:
            messagebox.showerror("Database error", str(e))
            cal, created = None, None
        self.calibration = cal
        if cal:
            self.counter = rc.RepCounter(cal, config.thresholds_for(self.ex_var.get()))
            self.counter.resync(self.raw_g)
            self.cal_var.set(f"Calibrated on {created[:16]}  (range {cal.span_deg:.0f}°)")
            self.cal_label.config(fg=GREEN)
        else:
            self.counter = None
            self.cal_var.set("Not calibrated yet")
            self.cal_label.config(fg=RED)
        if announce:
            self.speaker.say(f"{self.user_var.get()}, {self.ex_var.get()}, {self.arm_var.get()} arm. " +
                             ("Calibration loaded." if cal else "Please calibrate."))

    # =====================================================================
    # Calibration
    # =====================================================================
    def start_calibration(self):
        if self.in_set:
            self.msg_var.set("Stop the set before calibrating.")
            return
        if not self.data_ok:
            self.msg_var.set("Can't calibrate: the sensor isn't connected.")
            self.speaker.say("Can't calibrate. The sensor isn't connected.")
            return
        if not self.health.ok:
            self.msg_var.set("Can't calibrate: sensor problem. " + self.health.problem)
            self.speaker.say("Can't calibrate. Sensor problem.")
            return
        self._stop_rest()
        self.calibrator = rc.Calibrator(self.last_ms)

    def _calibrate_pressed(self):
        """The Calibrate button doubles as Cancel while calibrating."""
        if self.calibrator:
            self._cancel_calibration("Calibration cancelled.")
            self.speaker.say("Calibration cancelled.")
        else:
            self.start_calibration()

    def _cancel_calibration(self, message):
        """Stop a running calibration (no-op if none). The saved calibration is kept."""
        if not self.calibrator:
            return
        self.calibrator = None
        self._demo_mode("idle")
        self.msg_var.set(message + (" Your old calibration was kept." if self.calibration else ""))

    def on_calibration_event(self, kind, value):
        ex = config.EXERCISES[self.ex_var.get()]
        if kind == "step":
            self._demo_mode("end" if value.startswith("end") else "start")
            if value == "start_wait":
                self.speaker.say(f"Calibrating. Hold the start position, {ex['start']}, and keep still.")
            elif value == "end_wait":
                self.speaker.say(f"Now move to the end position, {ex['end']}, and hold still.")
            elif value.endswith("measure"):
                self.speaker.say("Measuring. Hold still.")
        elif kind == "done":
            self.calibrator = None
            self._demo_mode("idle")
            try:
                self.db.save_calibration(self.user_id, self.ex_var.get(), self.arm_var.get(), value)
            except sqlite3.Error as e:
                messagebox.showerror("Database error", f"Calibration worked but couldn't be saved:\n{e}")
                return
            self.load_calibration()
            self.msg_var.set(f"Calibrated! Range of motion: {value.span_deg:.0f}°. Press Next.")
            self.speaker.say(f"Calibration complete. Range {value.span_deg:.0f} degrees.")
        elif kind == "failed":
            self.calibrator = None
            self._demo_mode("idle")
            kept = " Your old calibration was kept." if self.calibration else ""
            self.msg_var.set("Calibration failed: " + value + kept)
            self.speaker.say("Calibration failed. " + value)

    # =====================================================================
    # Sets, rest timer and the workout
    # =====================================================================
    def _target_sets(self):
        try:
            return max(1, int(self.sets_var.get()))
        except (tk.TclError, ValueError):
            return config.DEFAULT_SETS

    def _target_reps(self):
        try:
            return max(1, int(self.reps_var.get()))
        except (tk.TclError, ValueError):
            return config.DEFAULT_REPS

    def _rest_seconds(self):
        try:
            return min(config.MAX_REST_S, max(config.MIN_REST_S, int(self.rest_var.get())))
        except (tk.TclError, ValueError):
            return config.DEFAULT_REST_S

    def start_set(self):
        if self.in_set:
            return
        problem = None
        if self.calibrator:
            problem = "Wait for calibration to finish."
        elif not self.calibration:
            problem = (f"{self.user_var.get()} isn't calibrated for {self.ex_var.get()} "
                       f"({self.arm_var.get()} arm) yet. Press Calibrate first.")
        elif not self.data_ok:
            problem = "The sensor isn't connected. Check the USB cable and press Reconnect."
        elif not self.health.ok:
            problem = "Sensor problem: " + self.health.problem
        if problem:
            self.msg_var.set("Can't start: " + problem)
            self.speaker.say("Can't start. " + problem.split(".")[0] + ".")
            return
        self._stop_rest()
        try:
            if self.workout_id is None:
                self.workout_id = self.db.start_workout(self.user_id, self.ex_var.get(), self.arm_var.get())
                self.completed_sets = 0
                self.workout_sets = []
        except sqlite3.Error as e:
            messagebox.showerror("Database error", str(e))
            return
        self.counter.reset()
        self.in_set = True
        self.set_started_pc = time.time()
        self.set_started_str = now_str()
        self._demo_mode("reps")
        n = self.completed_sets + 1
        self.msg_var.set(f"Set {n} in progress. Start from the start position: {config.EXERCISES[self.ex_var.get()]['start']}.")
        self.speaker.say(f"Set {n}. Go.")

    def stop_set(self):
        if self.in_set:
            self.end_set()

    def on_rep_event(self, kind, value):
        if kind == "rep":
            self.speaker.say(str(value.number))
            self.flash_reps("#fde68a" if value.momentum else "#bbf7d0")
            if value.number >= self._target_reps():
                self.end_set()
        elif kind == "missed_top":
            self.speaker.say("Incomplete. You didn't reach the top.")
            self.flash_reps("#fed7aa")
        elif kind == "not_extended":
            self.speaker.say("Incomplete. Go all the way back to the start.")
            self.flash_reps("#fed7aa")
        elif kind == "timeout":
            self.speaker.say("Rep took too long. Not counted.")
        elif kind == "momentum":
            self.speaker.say("Slow down, less swinging.")

    def end_set(self, announce=True, allow_rest=True):
        self.in_set = False
        self._demo_mode("idle")
        stats = self.counter.stats
        set_number = self.completed_sets + 1
        duration = time.time() - self.set_started_pc
        try:
            self.db.save_set(self.workout_id, self.user_id, self.ex_var.get(), self.arm_var.get(),
                             set_number, self.set_started_str, self._target_reps(), stats, duration)
        except sqlite3.Error as e:
            messagebox.showerror("Database error", f"Couldn't save the set:\n{e}")
        self.completed_sets = set_number
        self.workout_sets.append((set_number, stats.full_reps, self._target_reps(),
                                  stats.missed_top + stats.not_extended, stats.momentum_warnings,
                                  stats.avg_rom_deg))
        self.refresh_history()
        if announce:
            self.speaker.say(f"Set complete. {stats.full_reps} reps.")
        if self.completed_sets >= self._target_sets():
            if announce:
                self._finish_workout(show_summary=True)
        elif allow_rest:
            self.start_rest()
        self.msg_var.set(f"Set {set_number} saved: {stats.full_reps} of {self._target_reps()} reps.")

    def start_rest(self):
        self.resting = True
        self.rest_left = self._rest_seconds()
        self.skip_btn.pack(side="left", padx=16)
        self._rest_tick()

    def _rest_tick(self):
        if not self.resting:
            return
        n = self.completed_sets + 1
        if self.rest_left <= 0:
            self._stop_rest()
            self.msg_var.set(f"Rest over - press Start Set for set {n}.")
            self.speaker.say(f"Rest over. Start set {n}.")
            return
        self.rest_label.config(text=f"Rest  {self.rest_left // 60}:{self.rest_left % 60:02d}",
                               fg=RED if self.rest_left <= 10 else GREEN)
        if self.rest_left <= 10:
            self.speaker.say(str(self.rest_left))
        self.rest_left -= 1
        self.rest_job = self.root.after(1000, self._rest_tick)

    def skip_rest(self):
        if self.resting:
            self._stop_rest()
            self.msg_var.set(f"Rest skipped. Press Start Set for set {self.completed_sets + 1}.")

    def _stop_rest(self):
        self.resting = False
        if self.rest_job:
            self.root.after_cancel(self.rest_job)
            self.rest_job = None
        self.rest_label.config(text="")
        self.skip_btn.pack_forget()

    def _finish_workout(self, show_summary):
        if self.workout_id is None:
            return
        sets = self.workout_sets
        self.workout_id = None
        self.completed_sets = 0
        self.workout_sets = []
        if show_summary and sets:
            self.speaker.say("Workout complete. Great job!")
            total = sum(s[1] for s in sets)
            lines = [f"{self.user_var.get()} - {self.ex_var.get()} ({self.arm_var.get()} arm)", ""]
            for n, reps, target, inc, mom, rom in sets:
                lines.append(f"Set {n}: {reps}/{target} reps, {inc} incomplete, "
                             f"{mom} momentum warnings, avg ROM {rom:.0f}°")
            lines += ["", f"Total full reps: {total}"]
            self.last_summary = "\n".join(lines)
            if not getattr(self, "selftest", False):
                messagebox.showinfo("Workout complete", self.last_summary)

    # =====================================================================
    # Live display
    # =====================================================================
    def _refresh_live(self):
        target_reps = self._target_reps()
        c = self.counter
        reps = c.stats.full_reps if (c and (self.in_set or self.completed_sets)) else 0
        if self.in_set:
            set_n = self.completed_sets + 1
        else:
            set_n = min(self.completed_sets + 1, self._target_sets())
        self.set_var.set(f"Set {set_n} of {self._target_sets()}")
        self.reps_big.config(text=f"{reps} / {target_reps}",
                             fg=GREEN if reps >= target_reps else "#1d2330")
        if self.calibrator:
            step = self.calibrator.step
            ex = config.EXERCISES[self.ex_var.get()]
            secs = self.calibrator.seconds_left(self.last_ms)
            self.msg_var.set(CAL_STEP_TEXT[step].format(**ex) + f"  ({secs:.0f} s)")
            self.phase_var.set("Calibrating")
            self.progress["value"] = 0
        elif c and self.data_ok and self.health.ok:
            self.progress["value"] = max(0, min(100, c.progress))
            self.angle_var.set(f"{c.angle_deg:.0f}°")
            self.phase_var.set(rc.PHASE_LABELS[c.phase] if self.in_set else "Waiting")
        else:
            self.progress["value"] = 0
            self.angle_var.set("– °")
            self.phase_var.set("–")
        if c and (self.in_set or self.completed_sets):
            s = c.stats
            self.inc_var.set(str(s.missed_top + s.not_extended + s.timeouts))
            self.mom_var.set(str(s.momentum_warnings))
            self.last_var.set(f"{s.reps[-1].duration_s:.1f} s" if s.reps else "–")
        else:
            self.inc_var.set("0"); self.mom_var.set("0"); self.last_var.set("–")
        busy = self.calibrator is not None
        # Grey out buttons that can't be used right now, and say why
        why = ""
        if self.in_set:
            why = "Set in progress. Press Stop Set (or Space) to end it early."
        elif busy:
            why = "Calibrating... follow the instructions above."
        elif not self.calibration:
            why = "Start Set unlocks after you press Calibrate."
        elif not self.data_ok:
            why = "Start Set unlocks when the sensor is connected."
        elif not self.health.ok:
            why = "Start Set unlocks when the sensor problem is fixed."
        ready = not (self.in_set or busy) and self.calibration and self.data_ok and self.health.ok
        if self.why_var.get() != why:
            self.why_var.set(why)
            if why:
                self.why_label.grid()
            else:
                self.why_label.grid_remove()     # no empty line taking up space
        self._set_enabled(self.start_btn, bool(ready))
        self._set_enabled(self.stop_btn, self.in_set)
        # Calibrate button turns into Cancel while calibrating, so it can never get stuck
        look = ("■  Cancel calibration", GREY) if busy else \
               ("Calibrate again" if self.calibration else "Calibrate", BLUE)
        if getattr(self.cal_btn, "_look", None) != look:
            self.cal_btn._look = look
            self.cal_btn.color = look[1]
            if self.cal_btn.cget("state") == "normal":
                self.cal_btn.config(text=look[0], bg=look[1], activebackground=look[1])
            else:
                self.cal_btn.config(text=look[0], activebackground=look[1])
        self._set_enabled(self.cal_btn, not self.in_set)
        self._set_enabled(self.cal_next_btn, bool(self.calibration) and not busy)
        for b in self.back_btns:
            self._set_enabled(b, not (self.in_set or busy))
        self._show_tiles()
        hold = None
        if busy:
            hold = "end" if self.calibrator.step.startswith("end") else "start"
        if self.poses._hl != hold:
            self.poses.highlight(hold)
        if not self.health.ok or not self.data_ok:
            self._update_banner()

    # =====================================================================
    # Raw data recording
    # =====================================================================
    def toggle_recording(self):
        if self.record_var.get():
            os.makedirs(RAW_DIR, exist_ok=True)
            path = os.path.join(RAW_DIR, datetime.now().strftime("raw_%Y%m%d_%H%M%S.csv"))
            try:
                self.raw_file = open(path, "w", newline="")
            except OSError as e:
                messagebox.showerror("Record raw data", str(e))
                self.record_var.set(False)
                return
            self.raw_writer = csv.writer(self.raw_file)
            self.raw_writer.writerow(["pc_time", "esp_ms", "x_mv", "y_mv", "z_mv", "gx", "gy", "gz",
                                      "g_mag", "progress_pct", "phase", "in_set", "exercise", "arm"])
            self.msg_var.set(f"Recording raw data to {path}")
        else:
            self._close_recording()

    def _close_recording(self):
        if self.raw_file:
            self.raw_file.close()
            self.msg_var.set(f"Raw data saved to {self.raw_file.name}")
        self.raw_file = None
        self.raw_writer = None

    # =====================================================================
    # History tab
    # =====================================================================
    def _on_tab_changed(self, _event=None):
        tab = self.tabs.index(self.tabs.select())
        if tab == 1:
            self.refresh_history()
        elif tab == 2:
            self.prog_ex.set(self.ex_var.get())
            self.prog_arm.set(self.arm_var.get())
            self.refresh_progress()

    def refresh_history(self):
        ex = None if self.hist_ex.get() == "All" else self.hist_ex.get()
        arm = None if self.hist_arm.get() == "All" else self.hist_arm.get()
        try:
            self.hist_rows = self.db.sets_for_user(self.user_id, ex, arm)
        except sqlite3.Error as e:
            messagebox.showerror("Database error", str(e))
            return
        self.hist_tree.delete(*self.hist_tree.get_children())
        for r in self.hist_rows:
            self.hist_tree.insert("", "end", values=(
                r["started_at"][:16], r["exercise"], r["arm"], r["set_number"],
                f"{r['full_reps']} / {r['target_reps']}", r["missed_top"], r["not_extended"],
                r["momentum_warnings"], f"{r['avg_rep_s']:.2f}", f"{r['avg_rom_deg']:.0f}",
                f"{r['duration_s']:.0f}"))

    def export_csv(self):
        self.refresh_history()
        if not self.hist_rows:
            messagebox.showinfo("Export CSV", "There are no sets to export.")
            return
        default = f"cccare_{self.user_var.get().replace(' ', '_')}_history.csv"
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=default,
                                            filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        keys = ["started_at", "exercise", "arm", "set_number", "full_reps", "target_reps",
                "missed_top", "not_extended", "momentum_warnings", "avg_rep_s", "avg_rom_deg", "duration_s"]
        try:
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["user"] + keys)
                for r in self.hist_rows:
                    w.writerow([self.user_var.get()] + [r[k] for k in keys])
        except OSError as e:
            messagebox.showerror("Export CSV", str(e))
            return
        messagebox.showinfo("Export CSV", f"Saved {len(self.hist_rows)} sets to\n{path}")

    def clear_history(self):
        name = self.user_var.get()
        if not messagebox.askyesno("Clear history", f"Delete ALL saved sets for {name}?\n\n"
                                   "Calibrations are kept. This can't be undone."):
            return
        if self.in_set:
            self.end_set(announce=False, allow_rest=False)
        self._stop_rest()
        self._finish_workout(show_summary=False)
        try:
            self.db.clear_history(self.user_id)
        except sqlite3.Error as e:
            messagebox.showerror("Database error", str(e))
        self.refresh_history()

    # =====================================================================
    # Progress tab (matplotlib)
    # =====================================================================
    def refresh_progress(self):
        user = self.user_var.get()
        ex, arm = self.prog_ex.get(), self.prog_arm.get()
        other = "Left" if arm == "Right" else "Right"
        self.prog_title.config(text=f"Progress for {user}")
        try:
            mine = self.db.sessions(self.user_id, ex, arm)
            theirs = self.db.sessions(self.user_id, ex, other)
        except sqlite3.Error as e:
            messagebox.showerror("Database error", str(e))
            return

        self.fig.clear()
        axes = self.fig.subplots(3, 1, sharex=False)
        series = [(arm, mine, BLUE, "-o")]
        if mine and theirs:          # left vs right comparison
            series.append((other, theirs, "#f97316", "--s"))
        charts = [("total_reps", "Full reps in each workout"),
                  ("avg_rom", "How far the arm moved (degrees)"),
                  ("momentum", "Swinging warnings in each workout")]
        fs = 11 * self.fonts.scale
        for ax, (key, title) in zip(axes, charts):
            for name, rows, color, fmt in series:
                xs = list(range(1, len(rows) + 1))
                ys = [r[key] or 0 for r in rows]
                ax.plot(xs, ys, fmt, color=color, label=f"{name} arm", linewidth=2.5, markersize=7)
            ax.set_title(title, fontsize=fs + 2, loc="left", fontweight="bold")
            ax.xaxis.set_major_locator(MaxNLocator(integer=True))      # workout 1, 2, 3 (no 1.5)
            if key != "avg_rom":
                ax.yaxis.set_major_locator(MaxNLocator(integer=True))  # whole reps / warnings
                ax.set_ylim(0, max(1, ax.get_ylim()[1]))       # all zeros -> 0 to 1, not 0.000-0.054
            ax.tick_params(labelsize=fs)
            ax.grid(alpha=0.3)
            ax.spines[["top", "right"]].set_visible(False)
            if len(series) > 1:
                ax.legend(fontsize=fs, frameon=False)
        axes[-1].set_xlabel("Workout number (1 = first)", fontsize=fs)
        if not mine:
            axes[0].text(0.5, 0.5, f"No sets yet for {ex}, {arm} arm", ha="center", va="center",
                         transform=axes[0].transAxes, color=GREY, fontsize=fs + 2)
        self.fig.tight_layout()
        self.canvas.draw()

        if mine and theirs:
            def avg(rows, key):
                vals = [r[key] or 0 for r in rows]
                return sum(vals) / len(vals)
            self.compare_var.set(
                f"Left vs right - average reps per session: {arm} {avg(mine, 'total_reps'):.1f}, "
                f"{other} {avg(theirs, 'total_reps'):.1f}.   Average ROM: {arm} {avg(mine, 'avg_rom'):.0f}°, "
                f"{other} {avg(theirs, 'avg_rom'):.0f}°")
        else:
            self.compare_var.set(f"Sessions: {len(mine)}. (A left vs right comparison appears once both arms have data.)")

    # =====================================================================
    def on_close(self):
        if self.in_set:
            self.end_set(announce=False, allow_rest=False)
        self._close_recording()
        if self.source:
            self.source.stop()
        self.root.destroy()


# =========================================================================
# Self-test: drives the real UI in demo mode, start to finish
# =========================================================================
def run_selftest():
    import tempfile
    global save_settings
    save_settings = lambda settings: None   # noqa: E731  (don't change the user's settings)
    db_path = os.path.join(tempfile.mkdtemp(), "selftest.db")
    root = tk.Tk()
    app = TrackerApp(root, demo=True, db_path=db_path)
    app.selftest = True
    app.speaker.muted = False
    app.sets_var.set(2)
    app.reps_var.set(4)
    errors = []
    root.report_callback_exception = lambda *exc: errors.append(exc)
    start = time.time()
    state = {"step": "calibrate"}

    def drive():
        s = state["step"]
        if errors or time.time() - start > 150:
            print("SELFTEST FAILED", errors or "timeout in step " + s)
            app.on_close()
            sys.exit(1)
        if s == "calibrate" and app.data_ok and time.time() - start > 1.5:
            # Setup page: add a person, switch away, then pick them again
            bob = app.db.add_user("Bob"); app._load_users(select_id=bob)
            app._pick_user(app.users[0][0]); app._pick_user(bob)
            assert app.user_id == bob and app.user_var.get() == "Bob"
            app._pick_exercise("Lateral raise")
            app.go_page("calibrate"); assert app.page == "calibrate"
            # Reconnect in the middle of calibrating: must not get stuck
            app.start_calibration(); root.update()
            app.start_source()
            assert app.calibrator is None, "calibration should stop on reconnect"
            state["step"] = "reconnected"; state["t"] = time.time()
        elif s == "reconnected" and app.data_ok and time.time() - state["t"] > 1.0:
            app._pick_user(app.users[0][0]); app._pick_user(app.users[1][0])   # tiles unlocked again
            assert app.user_var.get() == "Bob"
            app.go_page("calibrate")
            app.start_calibration(); state["step"] = "wait_cal"
        elif s == "wait_cal" and not app.calibrator:
            print("calibration:", app.cal_var.get())
            assert app.calibration, app.msg_var.get()
            app._next_from_calibrate(); assert app.page == "exercise"
            for on in (True, False, True):                 # arm angle setting
                app.show_angle_var.set(on); app._toggle_angle(); root.update()
                assert bool(app.angle_box.winfo_ismapped()) == on
            app.start_set(); state["step"] = "set1"
        elif s == "set1" and not app.in_set:
            assert app.resting, "rest timer should be running"
            app.skip_rest(); app.start_set(); state["step"] = "set2"
        elif s == "set2" and not app.in_set:
            print(getattr(app, "last_summary", "no summary"))
            rows = app.db.sets_for_user(app.user_id)
            for r in rows:
                print("db row:", {k: r[k] for k in ("exercise", "arm", "set_number", "full_reps",
                                                     "missed_top", "not_extended", "momentum_warnings",
                                                     "avg_rep_s", "avg_rom_deg")})
            assert len(rows) == 2
            app.tabs.select(1); root.update()
            app.tabs.select(2); root.update()
            for topic in manual.GuideView.TOPICS:          # every Guide page draws without errors
                app.show_guide(topic); root.update()
            assert not errors, errors
            print("history rows:", len(app.hist_tree.get_children()), "| progress:", app.compare_var.get())
            if app.speaker.error or not app.speaker.available:
                print("SELFTEST FAILED: text-to-speech:", app.speaker.error)
                app.on_close()
                sys.exit(2)
            print("SELFTEST PASSED in %.0f s" % (time.time() - start))
            app.on_close()
            return
        root.after(200, drive)

    root.after(200, drive)
    root.mainloop()


def use_sharp_text():
    """Tell Windows this app handles display scaling itself, so text isn't blurry."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main():
    use_sharp_text()
    if "--selftest" in sys.argv:
        run_selftest()
        return
    root = tk.Tk()
    TrackerApp(root, demo="--demo" in sys.argv)
    root.mainloop()


if __name__ == "__main__":
    main()
