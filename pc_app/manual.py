"""The built-in user guide: exercise pictures and instructions (no tracking logic).

* draw_pose(canvas, exercise, pose, ...) draws a stick figure in the START or END
  position, with the sensor shown in orange on the right part of the arm.
* GuideView is the "Guide" tab: a list of topics on the left and the page on the right.

Angles in the figure data: 0 = arm pointing straight down, 90 = pointing to the right
of the picture, 180 = straight up, -90 = to the left.
"""

import math
import tkinter as tk
from tkinter import ttk

import config

# Colours used in the pictures (fixed, high contrast)
INK = "#111827"
FAINT = "#b6bec9"
SENSOR = "#e0590f"
ACCENT = "#1d4ed8"
ACCENT_FILL = "#dbe6fb"
PAPER = "#ffffff"
FLOOR = "#cbd5e1"

UPPER, FORE = 42, 40   # arm segment lengths in picture units (picture is 220 x 240)

# ---------------------------------------------------------------------------
# Pose data for each exercise (matches the six exercises in config.EXERCISES)
# ---------------------------------------------------------------------------
FIGURES = {
    "Bicep curl": dict(hip=(100, 160), torso=180, legs=[(38, 0, 38, 0)], ground=238, sensor="fore",
                       start=(0, 0), end=(8, 152)),
    "Hammer curl": dict(hip=(100, 160), torso=180, legs=[(38, 0, 38, 0)], ground=238, sensor="fore",
                        start=(0, 0), end=(8, 148)),
    "Lateral raise": dict(front=True, hip=(110, 168), torso=180, legs=[(34, -8, 34, 0), (34, 8, 34, 0)],
                          ground=238, sensor="upper", start=(14, 14), end=(88, 90)),
    "Dumbbell overhead press": dict(front=True, hip=(110, 178), torso=180,
                                    legs=[(30, -8, 28, 0), (30, 8, 28, 0)], ground=238, sensor="upper",
                                    start=(95, 180), end=(170, 174)),
    "Dumbbell bench press": dict(hip=(150, 140), torso=-90, legs=[(46, 58, 46, 0)], ground=220,
                                 bench=(30, 146, 180, 158), sensor="upper", start=(75, 180), end=(180, 180)),
    "Dumbbell row": dict(hip=(62, 118), torso=104, legs=[(45, 12, 46, -4)], ground=212,
                         bench=(150, 176, 212, 188), support=(26, 2), sensor="upper",
                         start=(0, 0), end=(-68, -4)),
}

# ---------------------------------------------------------------------------
# Guide text for each exercise
# ---------------------------------------------------------------------------
EXERCISE_GUIDE = {
    "Bicep curl": dict(
        strap=["Strap the sensor on the inside of the forearm, near the wrist.",
               "Palm faces forward (up as you curl).",
               "Keep your elbow pinned at your side. Only the forearm moves."],
        tips=["Lower all the way until the arm is straight before the next curl.",
              "The count is spoken on the way down, when your arm is nearly straight."],
        errors=[("Incomplete. You didn't reach the top.", "Curl higher, so the dumbbell comes up to the shoulder."),
                ("Incomplete. Go all the way back to the start.", "Straighten the elbow fully at the bottom."),
                ("Slow down, less swinging.", "Don't lean back or jerk the weight. Take about a second to lift.")]),
    "Hammer curl": dict(
        strap=["Same spot as the bicep curl: forearm, near the wrist.",
               "Palm faces your body, thumb pointing up, for the whole rep.",
               "Hammer curl has its own calibration. Calibrate it separately."],
        tips=["Don't turn your wrist during the rep.", "Keep your elbow at your side."],
        errors=[("Incomplete. You didn't reach the top.", "Bring the dumbbell up to shoulder height."),
                ("Incomplete. Go all the way back to the start.", "Lower until the arm is straight."),
                ("Slow down, less swinging.", "Keep your body still and lift with control.")]),
    "Lateral raise": dict(
        strap=["Strap the sensor on the outside of the upper arm, halfway between shoulder and elbow.",
               "The picture is from the front. Raise the arm out to the side, not forward.",
               "Keep a slight bend in the elbow, and keep it the same throughout."],
        tips=["Stop at shoulder height, with the arm level with the floor.",
              "Lower slowly back to your side before the next rep."],
        errors=[("Incomplete. You didn't reach the top.", "Lift until the arm is level with your shoulder."),
                ("Incomplete. Go all the way back to the start.", "Bring the arm right back down to your side."),
                ("Slow down, less swinging.", "Don't shrug or bounce the weight up.")]),
    "Dumbbell overhead press": dict(
        strap=["Put the sensor on the UPPER arm, not the forearm. In a press the forearm stays upright and hardly tilts.",
               "The picture is from the front. Start with the elbow out to the side and the dumbbell at shoulder height.",
               "Sit or stand tall with a straight back."],
        tips=["Press until the elbow is straight, above the shoulder.",
              "Come all the way back down to shoulder height each time."],
        errors=[("Incomplete. You didn't reach the top.", "Fully straighten the arm overhead."),
                ("Incomplete. Go all the way back to the start.", "Lower the dumbbell to shoulder level."),
                ("Slow down, less swinging.", "Don't bend your knees to push the weight up.")]),
    "Dumbbell bench press": dict(
        strap=["Sensor on the UPPER arm, as for the overhead press.",
               "Lie on the bench with your feet flat on the floor. Calibrate while lying down.",
               "Start with the dumbbell at chest level."],
        tips=["Push straight up until the arm is fully extended.",
              "Have someone nearby to help (a spotter)."],
        errors=[("Incomplete. You didn't reach the top.", "Straighten the arm fully above the chest."),
                ("Incomplete. Go all the way back to the start.", "Lower the dumbbell back to chest level."),
                ("Slow down, less swinging.", "Don't bounce the weight off the bottom.")]),
    "Dumbbell row": dict(
        strap=["Sensor on the back of the upper arm.",
               "Bend forward with a flat back and rest your other hand on a bench.",
               "Let the arm hang straight down to start."],
        tips=["Pull the elbow back past your body, close to your side.",
              "Keep your body still. Only the arm moves."],
        errors=[("Incomplete. You didn't reach the top.", "Pull the elbow higher, toward your hip."),
                ("Incomplete. Go all the way back to the start.", "Let the arm hang straight between reps."),
                ("Slow down, less swinging.", "Don't twist your body to pull the weight.")]),
}

GETTING_STARTED = [
    ("Plug in the sensor",
     "Connect the tracker to this computer with the USB cable. The box in the top-left corner turns "
     "GREEN and says \"Sensor connected\". No sensor? Open \"Settings\" and tick \"Demo mode\" to practise."),
    ("Page 1: Setup",
     "Press your name (new here? press \"+ Add person\"), the exercise and the arm. Set how many "
     "sets and reps you want with the - and + buttons. Then press \"Next\"."),
    ("Page 2: Calibrate (only the first time)",
     "The pictures show where to strap the sensor. Press \"Calibrate\" and listen: hold the START "
     "position still, then the END position. The tracker remembers it, so next time you can just press \"Next\"."),
    ("Page 3: Exercise",
     "Press the green \"Start Set\" button. The tracker counts every full rep out loud and stops "
     "by itself when you reach your target."),
    ("Rest, then the next set",
     "A rest clock counts down. When it says \"Rest over\", press \"Start Set\" again. "
     "After the last set you see a summary. Press \"Back\" to change anything."),
]

SCREEN_HELP = [
    ("1  2  3 at the top", "Which page you are on: Setup, Calibrate, Exercise. Use \"Next\" and \"Back\" to move between them."),
    ("Big number", "Reps done in this set / reps you are aiming for. It turns green when you reach the target."),
    ("Blue bar", "How far through the movement you are. Empty at the START position, full at the END."),
    ("Right now", "What the tracker sees: Ready, Lifting, At the top, or Lowering."),
    ("Incomplete reps", "Reps that didn't go all the way. These are not counted."),
    ("Swinging", "Reps where the weight was swung. These are counted, with a reminder to slow down."),
    ("Arm angle", "Hidden unless you turn on \"Show arm angle\" in Settings."),
    ("Mute voice", "Turns the spoken messages off. Tick it again to turn them back on."),
    ("Settings", "Show arm angle, Demo mode, the USB port, and recording raw data."),
    ("Keyboard", "Space bar = start or stop a set (Exercise page).  C = calibrate (Calibrate page)."),
]

TROUBLE = [
    ("The box at the top is red: \"Sensor disconnected\"",
     "Check the USB cable is pushed in at both ends, then press \"Reconnect\". If the Arduino "
     "program is open, close it, because only one program can use the sensor at a time."),
    ("A red bar says \"Sensor problem\"",
     "A wire inside the sensor is loose. Ask a helper to check the wiring. Counting starts again "
     "by itself once the readings are normal."),
    ("\"Calibration failed: you moved\"",
     "Rest your arm against something and hold it completely still during the 1-second "
     "measurement. Your old calibration is kept."),
    ("\"Calibration failed: the arm only moved ... degrees\"",
     "Tighten the strap, check the sensor is on the right part of the arm (see the pictures), "
     "and move all the way to the END position."),
    ("Reps are not being counted",
     "Go back to the START position until \"Right now\" says Ready. If the strap slipped, "
     "press \"Calibrate\" again."),
    ("The Start Set button is grey",
     "Read the line under the button, which says what is missing. Usually you need to calibrate first."),
]


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def _dir(a):
    r = math.radians(a)
    return math.sin(r), math.cos(r)


def _move(p, length, a):
    d = _dir(a)
    return p[0] + d[0] * length, p[1] + d[1] * length


def _rotated_rect(cx, cy, w, h, ang):
    """Corners of a w x h rectangle centred on (cx, cy), long side along angle ang."""
    ux, uy = _dir(ang)           # along the segment
    vx, vy = -uy, ux             # across it
    pts = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        pts += [cx + ux * sx * w / 2 + vx * sy * h / 2, cy + uy * sx * w / 2 + vy * sy * h / 2]
    return pts


def screen_scale(widget):
    """How many screen pixels one "normal" pixel is on this display (1.0 at 100%, 1.5 at 150%)."""
    try:
        return max(1.0, widget.winfo_fpixels("1i") / 96.0)
    except Exception:
        return 1.0


def draw_pose(canvas, exercise, pose, x0=0, y0=0, scale=1.0, label_sensor=True, text_scale=None):
    """Draw `exercise` in pose "start" or "end" on a tk Canvas.

    The picture is 220 x 240 units, drawn at (x0, y0) and multiplied by `scale`.
    text_scale sizes the "SENSOR" label (in points, so it shouldn't include screen DPI).
    """
    if text_scale is None:
        text_scale = scale
    f = FIGURES[exercise]
    S = lambda p: (x0 + p[0] * scale, y0 + p[1] * scale)          # noqa: E731
    w = lambda n: max(1, n * scale)                                 # noqa: E731

    def line(a, b, color=INK, width=6, dash=None):
        a, b = S(a), S(b)
        canvas.create_line(*a, *b, fill=color, width=w(width), capstyle="round", dash=dash)

    upper, fore = f[pose]
    hip = f["hip"]
    shoulder = _move(hip, 70, f["torso"])
    neck_end = _move(shoulder, 10, f["torso"])
    head = _move(shoulder, 24, f["torso"])

    if f.get("ground"):
        line((12, f["ground"]), (208, f["ground"]), FLOOR, 3)
    if f.get("bench"):
        x1, y1, x2, y2 = f["bench"]
        canvas.create_rectangle(*S((x1, y1)), *S((x2, y2)), fill=FLOOR, outline="")

    for thigh, a1, shin, a2 in f["legs"]:
        knee = _move(hip, thigh, a1)
        line(hip, knee); line(knee, _move(knee, shin, a2))
    line(hip, neck_end, width=7)
    hx, hy = S(head)
    r = 13 * scale
    canvas.create_oval(hx - r, hy - r, hx + r, hy + r, fill=PAPER, outline=INK, width=w(5))

    sh = (shoulder[0] + 18, shoulder[1] + 2) if f.get("front") else shoulder
    if f.get("front"):     # shoulder line and the other arm, drawn faintly
        sh_l = (shoulder[0] - 18, shoulder[1] + 2)
        line(sh_l, sh, width=7)
        e = _move(sh_l, UPPER, -upper)
        line(sh_l, e, FAINT); line(e, _move(e, FORE, -fore), FAINT)
    if f.get("support"):
        e = _move(sh, UPPER, f["support"][0])
        line(sh, e, FAINT); line(e, _move(e, FORE, f["support"][1]), FAINT)

    elbow = _move(sh, UPPER, upper)
    hand = _move(elbow, FORE, fore)

    if pose == "end":      # show the angle travelled by the sensor's part of the arm
        s_upper, s_fore = f["start"]
        if f["sensor"] == "fore":
            pivot, a1, a2, length = elbow, s_fore, fore, FORE
        else:
            pivot, a1, a2, length = sh, s_upper, upper, UPPER
        px, py = S(pivot)
        rr = 26 * scale
        canvas.create_arc(px - rr, py - rr, px + rr, py + rr, start=a1 - 90, extent=a2 - a1,
                          fill=ACCENT_FILL, outline=ACCENT, width=w(2), style="pieslice")
        line(pivot, _move(pivot, length + 6, a1), ACCENT, 3, dash=(4, 4))

    line(sh, elbow); line(elbow, hand)
    # dumbbell: a bar across the hand with a plate at each end
    across = fore + 90
    hw = 13 if f.get("front") else 9
    p1, p2 = _move(hand, hw, across), _move(hand, -hw, across)
    line(p1, p2, "#6b7280", 5)
    for p in (_move(hand, hw + 3, across), _move(hand, -hw - 3, across)):
        cx, cy = S(p)
        canvas.create_polygon(*_rotated_rect(cx, cy, 7 * scale, 13 * scale, across),
                              fill="#6b7280", outline="")

    # the sensor
    a, b, ang = (elbow, hand, fore) if f["sensor"] == "fore" else (sh, elbow, upper)
    m = (a[0] + (b[0] - a[0]) * 0.55, a[1] + (b[1] - a[1]) * 0.55)
    cx, cy = S(m)
    canvas.create_polygon(*_rotated_rect(cx, cy, 22 * scale, 13 * scale, ang),
                          fill=SENSOR, outline=PAPER, width=w(1.5))
    if label_sensor and pose == "start":
        # label on whichever side has more room, so it never runs off the picture
        side = 1 if m[0] < 130 else -1
        canvas.create_text(cx + 16 * scale * side, cy, text="SENSOR", fill=SENSOR,
                           anchor="w" if side > 0 else "e",
                           font=("Segoe UI", max(7, round(9 * text_scale)), "bold"))


class PosePair(tk.Frame):
    """START and END pictures side by side, with a caption under each.

    highlight("start" / "end" / None) draws a thick blue frame around one picture,
    used during calibration to show which position to hold.
    """

    def __init__(self, parent, fonts, size=1.0, **kw):
        super().__init__(parent, **kw)
        self.fonts = fonts
        self.size = size
        self.exercise = None
        self._hl = None
        self.boxes = {}
        for col, pose in enumerate(("start", "end")):
            box = tk.Frame(self, bg=PAPER, highlightthickness=4, highlightbackground=PAPER)
            box.grid(row=0, column=col, padx=4, sticky="n")
            title = tk.Label(box, bg=PAPER, fg=INK, font=fonts.get(13, "bold"))
            title.pack(pady=(6, 0))
            cv = tk.Canvas(box, bg=PAPER, highlightthickness=0)
            cv.pack(padx=6)
            cap = tk.Label(box, bg=PAPER, fg="#374151", font=fonts.get(12), justify="center")
            cap.pack(pady=(0, 8))
            self.boxes[pose] = (box, title, cv, cap)

    def show(self, exercise, size=None):
        if size is not None:
            self.size = size
        self.exercise = exercise
        ex = config.EXERCISES[exercise]
        s = self.size * screen_scale(self)      # bigger on high-resolution screens
        for pose in ("start", "end"):
            box, title, cv, cap = self.boxes[pose]
            title.config(text="START" if pose == "start" else "END")
            cap.config(text=ex[pose].capitalize(), wraplength=int(210 * s))
            cv.config(width=int(220 * s), height=int(240 * s))
            cv.delete("all")
            draw_pose(cv, exercise, pose, scale=s, text_scale=self.size * self.fonts.scale)
        self.highlight(self._hl)

    def highlight(self, pose):
        self._hl = pose
        for p, (box, title, _, _) in self.boxes.items():
            on = p == pose
            box.config(highlightbackground=ACCENT if on else PAPER)
            if on:
                title.config(text=("HOLD THIS:  START" if p == "start" else "HOLD THIS:  END"), fg=ACCENT)
            else:
                title.config(text="START" if p == "start" else "END", fg=INK)


# ---------------------------------------------------------------------------
# The Guide tab
# ---------------------------------------------------------------------------
class GuideView(ttk.Frame):
    TOPICS = ["Getting started", "What the screen shows"] + list(config.EXERCISES) + ["If something goes wrong"]

    def __init__(self, parent, fonts):
        super().__init__(parent, padding=10)
        self.fonts = fonts
        self.topic = self.TOPICS[0]

        menu = tk.Frame(self)
        menu.pack(side="left", fill="y", padx=(0, 12))
        tk.Label(menu, text="Choose a topic", font=fonts.get(14, "bold")).pack(anchor="w", pady=(0, 6))
        self.topic_btns = {}
        for t in self.TOPICS:
            b = tk.Button(menu, text=t, anchor="w", justify="left", relief="flat", cursor="hand2",
                          font=fonts.get(13, "bold"), padx=14, pady=8, takefocus=0,
                          command=lambda t=t: self.show(t))
            b.pack(fill="x", pady=2)
            self.topic_btns[t] = b

        # scrollable page
        wrap = tk.Frame(self, bg=PAPER, highlightthickness=1, highlightbackground="#d1d5db")
        wrap.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg=PAPER, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.page = tk.Frame(self.canvas, bg=PAPER)
        self.page_id = self.canvas.create_window(0, 0, window=self.page, anchor="nw")
        self.page.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        self.width = 800

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120) * 3, "units")

    def _on_resize(self, event):
        self.canvas.itemconfigure(self.page_id, width=event.width)
        if abs(event.width - self.width) > 40:
            self.width = event.width
            self.show(self.topic)

    # --- page building helpers ---
    def _h1(self, text):
        tk.Label(self.page, text=text, bg=PAPER, fg=INK, font=self.fonts.get(24, "bold"),
                 anchor="w", justify="left").pack(fill="x", padx=24, pady=(20, 6))

    def _h2(self, text):
        tk.Label(self.page, text=text, bg=PAPER, fg=ACCENT, font=self.fonts.get(16, "bold"),
                 anchor="w").pack(fill="x", padx=24, pady=(18, 4))

    def _p(self, text, color=INK, size=14, indent=24, bold=False):
        tk.Label(self.page, text=text, bg=PAPER, fg=color, anchor="w", justify="left",
                 font=self.fonts.get(size, "bold" if bold else "normal"),
                 wraplength=max(300, self.width - indent - 60)).pack(fill="x", padx=(indent, 24), pady=2)

    def _numbered(self, items):
        for i, (title, text) in enumerate(items, 1):
            row = tk.Frame(self.page, bg=PAPER)
            row.pack(fill="x", padx=24, pady=8)
            tk.Label(row, text=str(i), bg=ACCENT, fg="white", width=2,
                     font=self.fonts.get(18, "bold")).pack(side="left", anchor="n", padx=(0, 14))
            body = tk.Frame(row, bg=PAPER)
            body.pack(side="left", fill="x", expand=True)
            tk.Label(body, text=title, bg=PAPER, fg=INK, font=self.fonts.get(15, "bold"),
                     anchor="w").pack(fill="x")
            tk.Label(body, text=text, bg=PAPER, fg="#374151", font=self.fonts.get(14), anchor="w",
                     justify="left", wraplength=max(300, self.width - 140)).pack(fill="x")

    def _pairs(self, items, left_color=INK):
        for a, b in items:
            row = tk.Frame(self.page, bg="#f3f4f6")
            row.pack(fill="x", padx=24, pady=4)
            tk.Label(row, text=a, bg="#f3f4f6", fg=left_color, font=self.fonts.get(14, "bold"),
                     anchor="w", justify="left", wraplength=max(200, self.width // 2 - 60)
                     ).pack(fill="x", padx=12, pady=(8, 0))
            tk.Label(row, text=b, bg="#f3f4f6", fg="#374151", font=self.fonts.get(14),
                     anchor="w", justify="left", wraplength=max(300, self.width - 100)
                     ).pack(fill="x", padx=12, pady=(0, 8))

    # --- pages ---
    def show(self, topic):
        if topic not in self.TOPICS:
            topic = self.TOPICS[0]
        self.topic = topic
        for t, b in self.topic_btns.items():
            on = t == topic
            b.config(bg=ACCENT if on else "#e5e7eb", fg="white" if on else INK,
                     activebackground=ACCENT if on else "#d1d5db",
                     activeforeground="white" if on else INK)
        for w in self.page.winfo_children():
            w.destroy()

        if topic == "Getting started":
            self._h1("Getting started")
            self._p("Follow these steps every time you exercise. The tracker talks to you, "
                    "so turn the computer's sound up.", "#374151")
            self._numbered(GETTING_STARTED)
        elif topic == "What the screen shows":
            self._h1("What the screen shows")
            self._pairs(SCREEN_HELP)
        elif topic == "If something goes wrong":
            self._h1("If something goes wrong")
            self._p("The tracker pauses counting and tells you why. Nothing is counted while there is a problem.",
                    "#374151")
            self._pairs(TROUBLE, left_color="#b91c1c")
        else:
            self._exercise_page(topic)
        self.canvas.yview_moveto(0)

    def _exercise_page(self, name):
        ex = config.EXERCISES[name]
        g = EXERCISE_GUIDE[name]
        self._h1(name)
        self._p(f"Sensor goes on the {ex['sensor']}.", SENSOR, 15, bold=True)
        size = 1.25 if self.width > 900 * screen_scale(self) else 1.0
        pics = PosePair(self.page, self.fonts, size=size, bg=PAPER)
        pics.pack(anchor="w", padx=20, pady=8)
        pics.show(name)
        self._p("Orange = where the sensor goes.  Blue = how far the arm moves "
                "(the faint dashed line is the START position).", "#6b7280", 12)

        self._h2("1.  Strap on the sensor")
        for t in g["strap"]:
            self._p("•  " + t)
        self._h2("2.  Calibrate (first time only)")
        self._numbered([
            ("Press Calibrate", f"Choose \"{name}\" and the arm on the Setup page, press Next, then press the blue Calibrate button."),
            ("Hold the START position", f"You will hear: \"Hold the start position, {ex['start']}, and keep still.\" "
                                        "You have 3 seconds to get there, then keep still for 1 second."),
            ("Hold the END position", f"You will hear: \"Now move to the end position, {ex['end']}, and hold still.\" "
                                      "Again: 3 seconds to get there, then 1 second still."),
            ("Done", "You will hear \"Calibration complete\". It's saved, so you won't need to do it again."),
        ])
        self._h2("3.  During the set")
        for t in g["tips"]:
            self._p("•  " + t)
        self._h2("If you hear...")
        self._pairs(g["errors"], left_color=ACCENT)
        tk.Frame(self.page, bg=PAPER, height=30).pack()
