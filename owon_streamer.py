#!/usr/bin/env python3

import os
import re
import math
import time
import queue
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox
import usb.core
import usb.util

APP_NAME = "USB Streamer"
APP_VERSION = "1.0"

# Help menu texts (dummy values, change them later). Shown only as text,
# nothing is opened.
CONTACT_EMAIL = "your.name@example.com"
GIT_REPO = "https://github.com/yourname/owon-multimeter-tool"

VID = 0x5345
PID = 0x1234

# Delay between readings. Slower polling is safe: it only means fewer queries.
POLL_MS = 1000

# Continuity: closed circuit if 0 < resistance < this many ohms.
# (This meter reports 0 for an open circuit, same as in diode mode.)
CONT_THRESHOLD_OHM = 50

# Readings smaller than this (in base units) are shown as 0.
# Hides the +-1 count flicker of the meter's lowest range (0.1 mV).
ZERO_DEADBAND = {
    "DC VOLTAGE": 0.0002,
    "AC VOLTAGE": 0.0002,
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MODES = {
    "DC VOLTAGE":  (":DMM:CONFigure:VOLTage DC", ":DMM:CONFigure?"),
    "AC VOLTAGE":  (":DMM:CONFigure:VOLTage AC", ":DMM:CONFigure?"),
    "DC CURRENT":  (":DMM:CONFigure:CURRent DC", ":DMM:CONFigure?"),
    "AC CURRENT":  (":DMM:CONFigure:CURRent AC", ":DMM:CONFigure?"),
    "RESISTANCE":  (":DMM:CONFigure RESistance", ":DMM:CONFigure?"),
    "DIODE":       (":DMM:CONFigure DIODe", ":DMM:CONFigure?"),
    "CAPACITANCE": (":DMM:CONFigure CAPacitance", ":DMM:CONFigure?"),
    "CONTINUITY":  (":DMM:CONFigure CONTinuity", ":DMM:CONFigure?"),
}

# RANGE / AUTO buttons, same behaviour as the meter's own keys:
#   RANGE: 1st click = leave auto (manual), next clicks = next range
#   AUTO : back to auto range
#
# The HDS200 firmware has :DMM:AUTO and :DMM:RANGe commands, but I could not
# find their parameters in any document. The values below are GUESSES.
# The log prints what the meter answers, so they are easy to correct.
AUTO_ON_CMD = ":DMM:AUTO ON"
AUTO_OFF_CMD = ":DMM:AUTO OFF"
RANGE_QUERY = ":DMM:RANGe?"       # the meter answers e.g. '2V' or '20nF'
RANGE_SET = ":DMM:RANGe {r}"      # {r} = range text, same style as the answer

# Modes where the RANGE / AUTO buttons work. A probe on this meter found no
# way to change the range of resistance over USB (capacitance behaved the
# same), so those two are left out: the buttons grey out there.
RANGE_MODES = ("DC VOLTAGE", "AC VOLTAGE")

# Ranges in the order the RANGE button cycles through them (then wraps).
# Each step lists the spellings to try, in order (only one is sent at a time,
# the next one only if the meter did not take the first).
RANGE_STEPS = {
    "DC VOLTAGE": [("2V",), ("20V",), ("200V",), ("1000V",)],
    "AC VOLTAGE": [("2V",), ("20V",), ("200V",), ("1000V",)],
}

SI_PREFIX = {
    "p": -12, "n": -9, "u": -6, "µ": -6, "μ": -6, "m": -3,
    "": 0, "k": 3, "K": 3, "M": 6, "G": 9,
}

RANGE_RE = re.compile(
    r'^\s*([0-9]*\.?[0-9]+)\s*([pnuµμmkKMG]?)\s*(.*?)\s*$'
)

# All the ways a meter might write "ohm"
OHM_NAMES = ("Ω", "Ω", "ohm", "ohms", "r")


def parse_range(text):
    """'20nF' -> (2e-08, 'F', 'u'). Returns None if it can't be parsed."""

    match = RANGE_RE.match(str(text))

    if not match:
        return None

    number, prefix, unit = match.groups()

    micro = "µ" if "µ" in str(text) else "u"

    return float(number) * 10 ** SI_PREFIX[prefix], unit, micro


def _unit_name(unit):

    unit = unit.strip()

    if unit in ("Ω", "Ω") or unit.lower() in ("ohm", "ohms", "r"):
        return "ohm"

    return unit.lower()


def same_range(a, b):
    """True if two range texts mean the same range ('1kV' == '1000V',
    '2kohm' == '2kΩ'). A missing unit matches any unit."""

    pa, pb = parse_range(a), parse_range(b)

    if pa is None or pb is None:
        return False

    ua, ub = _unit_name(pa[1]), _unit_name(pb[1])

    return (abs(pa[0] - pb[0]) <= 1e-9 * max(abs(pa[0]), 1e-12)
            and (ua == ub or not ua or not ub))

# Base unit for each mode (the meter returns base units: V, A, ohm, F)
UNITS = {
    "DC VOLTAGE": "V",
    "AC VOLTAGE": "V",
    "DC CURRENT": "A",
    "AC CURRENT": "A",
    "RESISTANCE": "\u03a9",     # Ω
    "DIODE": "V",
    "CAPACITANCE": "F",
}

PREFIXES = {
    -12: "p",
    -9: "n",
    -6: "\u00b5",               # µ
    -3: "m",
    0: "",
    3: "k",
    6: "M",
    9: "G",
}

NUMBER_RE = re.compile(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?')

# Model names look like HDS242, HDS242S, HDS245 ... (serial numbers don't match)
MODEL_RE = re.compile(r'\b[A-Z]{2,4}\d{2,4}[A-Z]{0,2}\b', re.I)

IDLE_BG = "#283341"       # button colours
ACTIVE_BG = "#167ac6"     # selected mode / active auto or manual

GREEN = "#43e18a"
RED = "#ff4d4d"
AMBER = "#ffc45c"
MONO = "DejaVu Sans Mono"


# ============================================================
# NUMBER FORMATTING (engineering notation + unit)
# ============================================================

def format_reading(raw, mode=None):
    """
    0.0000502   + CAPACITANCE -> 50.2 µF
    360600      + RESISTANCE  -> 360.6 kΩ
    5.02E-1     + DC VOLTAGE  -> 502 mV
    0.4623      + DIODE       -> 0.4623 V   (0 or overload -> OL)
    """

    raw = str(raw).strip()
    unit = UNITS.get(mode, "")

    match = NUMBER_RE.search(raw)

    # No number in the reply (e.g. "OL", errors): show it untouched.
    if match is None:
        return raw

    try:
        x = float(match.group())
    except ValueError:
        return raw

    # Overload values some meters send as huge numbers
    if abs(x) >= 9.9e37:
        return "OL"

    # Diode: the meter sends 0 when the junction is open / reversed
    if mode == "DIODE":

        if x <= 0:
            return "OL"

        return "%.4f V" % x

    # Hide single-count noise around zero
    if abs(x) < ZERO_DEADBAND.get(mode, 0):
        x = 0

    if x == 0:
        return ("0 " + unit).strip()

    exp = int(math.floor(math.log10(abs(x)) / 3) * 3)
    exp = max(-12, min(9, exp))

    scaled = x / (10 ** exp)
    text = "%.4g" % scaled

    # Rounding pushed it to 1000 -> move up one prefix
    if ("e" in text or abs(float(text)) >= 1000) and exp < 9:
        exp += 3
        scaled = x / (10 ** exp)
        text = "%.4g" % scaled

    return "%s %s%s" % (text, PREFIXES[exp], unit)


# ============================================================
# OWON USB COMMUNICATION
# ============================================================

class OWON:

    def __init__(self):

        self.lock = threading.RLock()

        self.dev = usb.core.find(
            idVendor=VID,
            idProduct=PID
        )

        if self.dev is None:
            raise RuntimeError("OWON device not found")

        self.detached = False

        try:

            if self.dev.is_kernel_driver_active(0):
                self.dev.detach_kernel_driver(0)
                self.detached = True

        except (NotImplementedError, usb.core.USBError):
            pass

        self.dev.set_configuration()

        cfg = self.dev.get_active_configuration()
        intf = cfg[(0, 0)]

        self.ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e:
                e.bEndpointAddress == 0x01
        )

        self.ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e:
                e.bEndpointAddress == 0x81
        )

        if self.ep_out is None or self.ep_in is None:
            raise RuntimeError("USB endpoints not found")

        usb.util.claim_interface(self.dev, 0)

    def send(self, command):

        with self.lock:

            self.ep_out.write(
                command.encode() + b"\n",
                timeout=3000
            )

    def _flush(self, timeout=20):
        """Drop any stale reply left in the IN endpoint, so a late answer
        to an earlier command is never mistaken for the next reading."""

        for _ in range(8):

            try:
                self.ep_in.read(64, timeout=timeout)

            except usb.core.USBError:
                return

    def query(self, command):

        with self.lock:

            self._flush()

            self.send(command)

            response = self.ep_in.read(
                64,
                timeout=3000
            )

            return bytes(response).decode(
                errors="replace"
            ).strip()

    def _usb_string(self, index):

        if not index:
            return ""

        try:
            return (usb.util.get_string(self.dev, index) or "").strip()

        except (usb.core.USBError, ValueError):
            return ""

    def identify(self):
        """
        Returns (model_name, info).
        The model is searched in the *IDN? reply and the USB descriptors.
        """

        idn = ""

        try:
            idn = self.query("*IDN?")

        except usb.core.USBError:
            pass

        info = {
            "IDN": idn,
            "USB manufacturer": self._usb_string(self.dev.iManufacturer),
            "USB product": self._usb_string(self.dev.iProduct),
            "USB serial": self._usb_string(self.dev.iSerialNumber),
        }

        text = " ".join([
            idn.replace(",", " "),
            info["USB product"],
            info["USB manufacturer"],
        ])

        found = MODEL_RE.search(text)

        if found:
            name = "OWON " + found.group(0).upper()

        else:
            name = "OWON"

        return name, info

    def set_mode(self, name):

        command, verify = MODES[name]

        with self.lock:

            self.send(command)

            time.sleep(0.3)

            response = self.query(verify)

            return command, response

    def read_range(self, valid=None, tries=4):
        """Ask the meter for its range. A late reply to an earlier command
        can arrive in place of this answer, so the answer is checked with
        valid(reply) and the question repeated if it doesn't fit."""

        reply = ""

        for _ in range(tries):

            with self.lock:

                self._flush(100)

                self.send(RANGE_QUERY)

                try:

                    reply = bytes(
                        self.ep_in.read(64, timeout=1000)
                    ).decode(errors="replace").strip()

                    # A stale reply may come first: keep reading, the real
                    # answer can follow right behind it.
                    extra = 0

                    while valid and not valid(reply) and extra < 3:

                        extra += 1

                        reply = bytes(
                            self.ep_in.read(64, timeout=300)
                        ).decode(errors="replace").strip()

                except usb.core.USBError:
                    pass

            if valid is None or valid(reply):
                return reply

            time.sleep(0.15)

        return reply

    def send_range(self, text):
        """Ask for a range (one command, no answer expected)."""

        self.send(RANGE_SET.format(r=text))

    def command(self, command):
        """Send a command and return the meter's answer, or '(no reply)'."""

        with self.lock:

            self._flush()

            self.send(command)

            try:
                response = self.ep_in.read(64, timeout=500)

                return bytes(response).decode(
                    errors="replace"
                ).strip()

            except usb.core.USBError:
                return "(no reply)"

    def measure(self):

        return self.query(":DMM:MEAS?")

    def close(self):

        with self.lock:

            try:
                usb.util.release_interface(self.dev, 0)
            except usb.core.USBError:
                pass

            usb.util.dispose_resources(self.dev)

            if self.detached:

                try:
                    self.dev.attach_kernel_driver(0)

                except (usb.core.USBError, NotImplementedError):
                    pass


# ============================================================
# TKINTER APPLICATION
# ============================================================

class App:

    def __init__(self, root):

        self.root = root

        root.title("%s v%s" % (APP_NAME, APP_VERSION))
        root.geometry("720x720")
        root.configure(bg="#15191f")

        self.instrument = None
        self.model = "OWON"
        self.running = True
        self.busy = False
        self.pending_mode = None
        self.pending_range = False
        self.pending_auto = False
        self.errors = 0

        self.current_mode = None
        self.last_text = None
        self.manual = {}

        self.log_all = tk.BooleanVar(value=False)


        # Worker threads never touch Tk directly: they queue callables here
        # and the main thread runs them.
        self.ui_queue = queue.Queue()

        self.build_menu()
        self.build_gui()

        # RANGE / AUTO start greyed out until a mode that has them is chosen
        self.set_controls(True)

        root.protocol("WM_DELETE_WINDOW", self.close)

        self.pump()
        self.connect()

    # ========================================================
    # THREAD-SAFE UI HELPERS
    # ========================================================

    def ui(self, fn):

        self.ui_queue.put(fn)

    def pump(self):

        while True:

            try:
                fn = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            try:
                fn()
            except tk.TclError:
                pass

        if self.running:
            self.root.after(50, self.pump)

    # ========================================================
    # MENUS
    # ========================================================

    def build_menu(self):

        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)

        file_menu.add_command(
            label="Export log (.txt)",
            command=self.export_log
        )

        file_menu.add_command(
            label="Copy log",
            command=self.copy_all
        )

        file_menu.add_command(
            label="Clear log",
            command=self.clear_log
        )

        file_menu.add_checkbutton(
            label="Log every reading",
            variable=self.log_all
        )

        file_menu.add_separator()

        file_menu.add_command(
            label="Exit",
            command=self.close
        )

        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)

        help_menu.add_command(
            label="About",
            command=self.show_about
        )

        help_menu.add_command(
            label="Contact",
            command=self.show_contact
        )

        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def show_about(self):

        messagebox.showinfo(
            "About",
            "%s  v%s\n\nDevice: %s\n\nGit: %s" % (
                APP_NAME, APP_VERSION, self.model, GIT_REPO
            )
        )

    def show_contact(self):

        messagebox.showinfo("Contact", "Email: " + CONTACT_EMAIL)

    # ========================================================
    # GUI
    # ========================================================

    def build_gui(self):

        # Bottom bar is packed first so it can never be pushed out of view
        log_buttons = tk.Frame(
            self.root,
            bg="#15191f"
        )

        log_buttons.pack(side="bottom", pady=8)

        tk.Button(
            log_buttons,
            text="EXPORT LOG (.txt)",
            command=self.export_log,
            bg="#384657",
            fg="white",
            relief="flat"
        ).pack(side="left", padx=5)

        tk.Button(
            log_buttons,
            text="CLEAR LOG",
            command=self.clear_log,
            bg="#384657",
            fg="white",
            relief="flat"
        ).pack(side="left", padx=5)

        # Title is replaced by the model read from the device
        self.title = tk.Label(
            self.root,
            text="%s v%s" % (APP_NAME, APP_VERSION),
            font=("Arial", 25, "bold"),
            fg="white",
            bg="#15191f"
        )

        self.title.pack(pady=(15, 8))

        self.status = tk.Label(
            self.root,
            text="USB: Connecting...",
            font=("Arial", 12),
            fg=AMBER,
            bg="#15191f"
        )

        self.status.pack(pady=8)

        frame = tk.Frame(
            self.root,
            bg="#15191f"
        )

        frame.pack(padx=15, pady=10, fill="x")

        buttons = [
            "DC VOLTAGE",
            "AC VOLTAGE",
            "DC CURRENT",
            "AC CURRENT",
            "RESISTANCE",
            "DIODE",
            "CAPACITANCE",
            "CONTINUITY"
        ]

        def make_button(text, command):

            return tk.Button(
                frame,
                text=text,
                font=("Arial", 10, "bold"),
                fg="white",
                bg=IDLE_BG,
                activebackground=ACTIVE_BG,
                activeforeground="white",
                disabledforeground="#8a94a0",
                relief="flat",
                height=2,
                command=command
            )

        self.mode_buttons = {}

        for i, name in enumerate(buttons):

            button = make_button(
                name,
                lambda n=name: self.change_mode(n)
            )

            button.grid(
                row=i // 2,
                column=i % 2,
                padx=5,
                pady=5,
                sticky="ew"
            )

            self.mode_buttons[name] = button

        # RANGE steps the range of the selected mode, AUTO goes back to auto
        self.range_btn = make_button("RANGE: AUTO", self.change_range)

        self.range_btn.grid(
            row=4,
            column=0,
            padx=5,
            pady=5,
            sticky="ew"
        )

        self.auto_btn = make_button("AUTO", self.set_auto)

        self.auto_btn.grid(
            row=4,
            column=1,
            padx=5,
            pady=5,
            sticky="ew"
        )

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        self.mode = tk.Label(
            self.root,
            text="No mode selected",
            font=("Arial", 15, "bold"),
            fg="#55b8ff",
            bg="#15191f"
        )

        self.mode.pack(pady=8)

        self.value = tk.Label(
            self.root,
            text="---",
            font=(MONO, 35, "bold"),
            fg=GREEN,
            bg="#15191f"
        )

        self.value.pack(pady=5)

        tk.Label(
            self.root,
            text="COMMAND / RESPONSE LOG",
            font=("Arial", 10, "bold"),
            fg="#9ba5b1",
            bg="#15191f"
        ).pack(pady=(10, 3))

        self.log = scrolledtext.ScrolledText(
            self.root,
            height=8,
            bg="#0e1217",
            fg=GREEN,
            insertbackground="white",
            font=(MONO, 10),
            relief="flat",
            wrap="word"
        )

        self.log.pack(
            fill="both",
            expand=True,
            padx=15,
            pady=5
        )

        self.make_log_copyable()

    # ========================================================
    # LOGGING
    # ========================================================

    def write_log(self, message):

        self.ui(lambda: self._append_log(message))

    def _append_log(self, message):

        self.log.insert("end", message + "\n")
        self.log.see("end")

    def make_log_copyable(self):
        """Select with the mouse, Ctrl+C / Ctrl+A, right-click menu.
        The log can't be typed into."""

        log = self.log

        # Visible selection colour (also when the widget isn't focused)
        log.config(
            selectbackground="#2a6fb0",
            selectforeground="white",
            inactiveselectbackground="#2a6fb0"
        )

        navigation = ("Left", "Right", "Up", "Down", "Home", "End",
                      "Prior", "Next")

        def on_key(event):

            ctrl = event.state & 0x4

            if ctrl and event.keysym.lower() == "c":
                return None

            if event.keysym in navigation:
                return None

            return "break"

        def select_all(event=None):

            log.tag_add("sel", "1.0", "end-1c")

            return "break"

        menu = tk.Menu(log, tearoff=0)

        menu.add_command(label="Copy", command=self.copy_selection)
        menu.add_command(label="Copy all", command=self.copy_all)
        menu.add_command(label="Select all", command=select_all)

        def popup(event):

            log.focus_set()
            menu.tk_popup(event.x_root, event.y_root)

            return "break"

        log.bind("<Key>", on_key)
        log.bind("<Control-a>", select_all)
        log.bind("<Control-A>", select_all)
        log.bind("<Button-1>", lambda e: log.focus_set())
        log.bind("<Button-3>", popup)
        log.bind("<Button-2>", popup)

    def copy_selection(self):

        try:
            text = self.log.get("sel.first", "sel.last")

        except tk.TclError:
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def copy_all(self):

        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get("1.0", "end-1c"))

    def clear_log(self):

        self.log.delete("1.0", "end")

    def export_log(self):

        # File name: date_time.txt, e.g. 2026-11-05_10-54-32.txt
        name = time.strftime("%Y-%m-%d_%H-%M-%S") + ".txt"
        content = self.log.get("1.0", "end-1c") + "\n"

        for folder in (BASE_DIR, os.path.expanduser("~")):

            path = os.path.join(folder, name)

            try:

                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)

                self.write_log("LOG EXPORTED: " + path)
                return

            except OSError as e:

                self.write_log("EXPORT ERROR (%s): %s" % (folder, e))

    # ========================================================
    # CONNECT
    # ========================================================

    def connect(self):

        try:

            self.instrument = OWON()

            self.model, info = self.instrument.identify()

            title = "%s %s v%s" % (self.model, APP_NAME, APP_VERSION)

            self.title.config(text=title)
            self.root.title(title)

            self.status.config(
                text="USB: CONNECTED",
                fg=GREEN
            )

            self.write_log("CONNECTED: " + self.model)

            self.poll()

        except Exception as e:

            self.status.config(
                text="USB ERROR: " + str(e),
                fg="#ff6060"
            )

            self.write_log("CONNECTION ERROR: " + str(e))

    # ========================================================
    # JOB BOOKKEEPING
    # ========================================================

    def finish_job(self):
        """Called from a worker thread when its USB job is done."""

        self.busy = False

        if self.pending_mode and self.running:

            name = self.pending_mode
            self.pending_mode = None

            self.ui(lambda: self.change_mode(name))

        elif self.pending_range and self.running:

            self.pending_range = False

            self.ui(self.change_range)

        elif self.pending_auto and self.running:

            self.pending_auto = False

            self.ui(self.set_auto)

        else:

            self.ui(lambda: self.set_controls(True))

    def set_controls(self, enabled):
        """Buttons are locked while the meter is being set, so fast clicks
        can't pile up commands."""

        state = "normal" if enabled else "disabled"

        for button in self.mode_buttons.values():

            button.config(state=state)

        # RANGE / AUTO only where the meter supports them
        has_range = enabled and self.current_mode in RANGE_MODES

        for button in (self.range_btn, self.auto_btn):

            button.config(state="normal" if has_range else "disabled")

    def highlight(self):
        """Blue = selected mode, AUTO when in auto, RANGE when in manual."""

        mode = self.current_mode
        manual = self.manual.get(mode, False)

        for name, button in self.mode_buttons.items():

            button.config(bg=ACTIVE_BG if name == mode else IDLE_BG)

        has_range = mode in RANGE_MODES

        self.auto_btn.config(
            bg=ACTIVE_BG if has_range and not manual else IDLE_BG
        )

        self.range_btn.config(
            bg=ACTIVE_BG if has_range and manual else IDLE_BG
        )

    def show(self, display):

        self.value.config(text=display[0], fg=display[1])

    def log_reading(self, display, force=False):
        """Log a measurement (only when it changes, unless 'log every
        reading' is on)."""

        text = display[0].replace("\n", "  ")

        if force or self.log_all.get() or text != self.last_text:

            self.write_log("%s  %s  %s" % (
                time.strftime("%H:%M:%S"), self.current_mode, text
            ))

        self.last_text = text

    # ========================================================
    # CHANGE RANGE
    # ========================================================

    def change_range(self):

        if self.instrument is None:
            return

        mode = self.current_mode

        if mode not in RANGE_MODES:

            self.write_log("RANGE: not available in this mode")
            return

        if self.busy:

            self.pending_range = True
            return

        first_click = not self.manual.get(mode, False)

        self.busy = True
        self.set_controls(False)

        def worker():

            try:

                if first_click:

                    # 1st click: leave auto range, keep the current range
                    self.instrument.send(AUTO_OFF_CMD)

                    time.sleep(0.25)

                    self.manual[mode] = True
                    self.ui(self.highlight)

                    # A range reads like '2V' or '20nF' (number + unit)
                    label = self.instrument.read_range(
                        lambda r: parse_range(r) is not None
                        and parse_range(r)[1] != ""
                    )

                    # Right after power-on the meter can ignore AUTO OFF, but
                    # it always goes manual when a range is set, so set the
                    # range it is already on (same range, now manual).
                    if parse_range(label) is not None:

                        self.instrument.send_range(label)

                        time.sleep(0.4)

                    state = self.instrument.command(":DMM:AUTO?")

                    self.write_log("RANGE: manual, %s (auto? %s)" % (
                        label, state
                    ))

                else:

                    # Next clicks: go to the next range up
                    label = self.next_range(mode)

                self.ui(lambda: self.range_btn.config(text="RANGE: " + label))

                time.sleep(0.3)

                result = self.instrument.measure()

                display = self.prepare_display(mode, result)

                self.ui(lambda: self.show(display))

                self.log_reading(display, force=True)

            except Exception as e:

                self.write_log("RANGE ERROR: " + str(e))

            finally:

                self.finish_job()

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    def next_range(self, mode):
        """Go to the next range in RANGE_STEPS (wraps after the last one).
        Every step is checked by reading the range back from the meter.
        Returns the range the meter reports at the end."""

        meter = self.instrument
        steps = RANGE_STEPS.get(mode)

        # Modes without a known list: just report what the meter says
        if not steps:

            current = meter.read_range()

            self.write_log("RANGE: meter reports %r" % current)

            return current or "?"

        def valid(reply):

            return any(same_range(reply, v) for s in steps for v in s)

        current = meter.read_range(valid)

        index = -1

        for i, s in enumerate(steps):

            if any(same_range(current, v) for v in s):
                index = i

        if index < 0:
            self.write_log("RANGE: unexpected range %r" % current)

        def go(step, wait):
            """Send ONE command (each command makes the meter beep) and wait
            until the meter reports that range. Never re-sent while waiting.
            Returns (confirmed, reported)."""

            reported = current

            for n, spelling in enumerate(step):

                meter.send_range(spelling)

                # the alternative spellings only get a short wait
                deadline = time.time() + (wait if n == 0 else 1.5)

                while time.time() < deadline:

                    time.sleep(0.4)

                    reported = meter.read_range(valid)

                    if any(same_range(reported, v) for v in step):

                        self.write_log("RANGE: %s -> %s -> now %s" % (
                            current, spelling, reported
                        ))

                        return True, reported

                self.write_log("RANGE: %s -> %s -> not taken (%s)" % (
                    current, spelling, reported
                ))

            return False, reported

        # Next range up
        ok, reported = go(steps[(index + 1) % len(steps)], 3.0)

        # Not taken: probably already on the top range, so wrap to the lowest
        if not ok and index != 0:

            self.write_log("RANGE: wrapping to the lowest range")

            ok, reported = go(steps[0], 3.0)

        return reported

    def set_auto(self):

        if self.instrument is None:
            return

        mode = self.current_mode

        if mode not in RANGE_MODES:

            self.write_log("AUTO: not available in this mode")
            return

        if self.busy:

            self.pending_auto = True
            return

        self.busy = True
        self.set_controls(False)

        def worker():

            try:

                reply = self.instrument.command(AUTO_ON_CMD)

                self.write_log("AUTO: %s -> %r" % (AUTO_ON_CMD, reply))

                self.manual[mode] = False

                self.ui(lambda: self.range_btn.config(text="RANGE: AUTO"))
                self.ui(self.highlight)

                time.sleep(0.3)

                result = self.instrument.measure()

                display = self.prepare_display(mode, result)

                self.ui(lambda: self.show(display))

                self.log_reading(display, force=True)

            except Exception as e:

                self.write_log("AUTO ERROR: " + str(e))

            finally:

                self.finish_job()

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    # ========================================================
    # CHANGE MODE
    # ========================================================

    def change_mode(self, name):

        if self.instrument is None:

            self.write_log("ERROR: USB NOT CONNECTED")
            return

        # A reading is in progress: remember the click and run it right after
        if self.busy:

            self.pending_mode = name
            return

        self.busy = True
        self.set_controls(False)
        self.current_mode = name
        self.last_text = None

        # Selecting a mode sends the plain config command (meter default range)
        self.manual[name] = False
        self.range_btn.config(text="RANGE: AUTO")
        self.highlight()

        self.mode.config(text=name)
        self.value.config(text="---", fg=GREEN)

        self.status.config(
            text="SENDING COMMAND...",
            fg=AMBER
        )

        def worker():

            try:

                self.instrument.set_mode(name)

                time.sleep(0.3)

                measurement = self.instrument.measure()

                display = self.prepare_display(name, measurement)

                self.errors = 0

                self.ui(lambda: self.show(display))

                self.log_reading(display, force=True)

                self.ui(lambda: self.status.config(
                    text="USB: CONNECTED",
                    fg=GREEN
                ))

            except Exception as e:

                self.write_log("ERROR: " + str(e))

                self.ui(lambda: self.status.config(
                    text="COMMAND ERROR",
                    fg="#ff6060"
                ))

            finally:

                self.finish_job()

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    # ========================================================
    # DISPLAY FORMATTING  ->  (text, colour)
    # ========================================================

    def prepare_display(self, mode, result):

        result = str(result).strip()

        # Continuity: green when closed, red when open.

        if mode == "CONTINUITY":

            match = NUMBER_RE.search(result)

            try:
                ohms = float(match.group()) if match else None
            except ValueError:
                ohms = None

            # Second line: the resistance the meter is measuring
            if ohms is not None and 0 < ohms < 9.9e37:
                value = format_reading(result, "RESISTANCE")

            else:
                value = "OL"

            if ohms is not None and 0 < ohms < CONT_THRESHOLD_OHM:
                return "CONTINUITY\n" + value, GREEN

            return "NO CONTINUITY\n" + value, RED

        # Everything else: engineering notation with prefix and unit.

        text = format_reading(result, mode)

        if text == "OL":
            return text, AMBER

        return text, GREEN

    # ========================================================
    # POLLING
    # ========================================================

    def poll(self):

        if not self.running or self.instrument is None:
            return

        if self.busy:

            self.root.after(500, self.poll)
            return

        self.busy = True

        def worker():

            try:

                result = self.instrument.measure()

                self.errors = 0

                display = self.prepare_display(self.current_mode, result)

                self.ui(lambda: self.show(display))

                if self.current_mode:
                    self.log_reading(display)

                self.ui(lambda: self.status.config(
                    text="USB: CONNECTED",
                    fg=GREEN
                ))

            except Exception as e:

                self.errors += 1

                self.write_log("POLL ERROR: " + str(e))

                if self.errors >= 3:

                    self.ui(lambda: self.status.config(
                        text="USB: NO RESPONSE",
                        fg="#ff6060"
                    ))

            finally:

                self.finish_job()

                if self.running:

                    self.ui(
                        lambda: self.root.after(POLL_MS, self.poll)
                    )

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    # ========================================================
    # CLOSE
    # ========================================================

    def close(self):

        self.running = False

        if self.instrument:

            try:
                self.instrument.close()
            except Exception:
                pass

            self.instrument = None

        self.root.destroy()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    root = tk.Tk()

    app = App(root)

    root.mainloop()
