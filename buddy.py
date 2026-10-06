"""
Local Buddy - a desktop companion that chats through Ollama.

Setup:
    pip install requests pillow
    optional: pip install pyttsx3                  (basic robotic voice)
    optional: pip install piper-tts sounddevice numpy   (nicer cartoon voice, see PIPER_MODEL)
    optional: pip install sounddevice numpy        (mic: listening animation)
    optional: pip install faster-whisper           (mic: understand your speech, runs locally)
    ollama pull llama3.2
    python buddy.py

Sprites are copied into a "sprites/<state>/" folder next to this script,
so they are remembered the next time you start the app.
States: idle, listening, thinking, processing, talking.
GIFs work too - every frame is used as an animation.
"""
import os
import sys
import wave
import subprocess
import json
import shutil
import threading
import queue
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, simpledialog

import requests
from collections import deque
from PIL import Image, ImageTk, ImageSequence

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None
try:
    import numpy as np
    import sounddevice as sd
except (ImportError, OSError):   # OSError: PortAudio missing
    np = sd = None
try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None

OLLAMA_URL = "http://localhost:11434"
DEFAULT_PERSONAS = {
    "Doraemon": (
        "You are Doraemon, a cheerful, kind robot cat from the 22nd century with a "
        "magical pocket full of gadgets. You love dorayaki. Talk like a warm, playful "
        "best friend, keep replies short (1-3 sentences), and occasionally mention a "
        "made-up gadget from your pocket. No emojis or markdown."
    ),
    "Study Buddy": (
        "You are a friendly, encouraging study buddy for a computer science student. "
        "Explain ideas simply with small everyday examples, ask one short question to "
        "check understanding, and keep replies to 1-3 sentences. No emojis or markdown."
    ),
    "Grumpy Pirate": (
        "You are a grumpy old pirate captain who secretly loves helping. Grumble, use "
        "pirate slang, and keep replies to 1-3 sentences. No emojis or markdown."
    ),
    "Calm Coach": (
        "You are a calm, supportive coach. Speak gently, reflect what the person says, "
        "and suggest one small next step. Keep replies to 1-3 sentences. No emojis or markdown."
    ),
}
STATES = ["idle", "listening", "thinking", "processing", "talking"]
FALLBACK = {"idle": "🐱", "listening": "👂", "thinking": "🤔", "processing": "⚙️", "talking": "💬"}
HINTS = {
    "idle": "resting",
    "listening": "hearing you",
    "thinking": "waiting for first words",
    "processing": "model is slow or loading",
    "talking": "replying",
}
THEME = "dark"          # "dark" or "light"
THEMES = {
    "dark": {"bg": "#0b1220", "surface": "#141c2e", "surface2": "#1f2a44", "text": "#e6edf7",
             "muted": "#8a97b0", "accent": "#38bdf8", "accent_text": "#06223a",
             "me": "#2563eb", "me_text": "#ffffff", "bot": "#1f2a44"},
    "light": {"bg": "#f3f7fb", "surface": "#ffffff", "surface2": "#e8f0f8", "text": "#14243a",
              "muted": "#6b7f96", "accent": "#0a8de0", "accent_text": "#ffffff",
              "me": "#0a8de0", "me_text": "#ffffff", "bot": "#e8f0f8"},
}
C = THEMES[THEME]
STATE_COLORS = {"idle": "#94a3b8", "listening": "#34d399", "thinking": "#fbbf24",
                "processing": "#fb923c", "talking": "#38bdf8"}
SLOW_AFTER = 4.0        # seconds before "thinking" becomes "processing"
STAGE_SIZE = 260        # sprite box in pixels
MAX_TURNS = 20          # messages of history sent to the model
FRAME_MS = 250          # animation speed
SAMPLE_RATE = 16000     # mic sample rate
SILENCE_END = 1.0       # seconds of quiet that ends your sentence
MIN_SPEECH = 0.4        # ignore sounds shorter than this (seconds)
WHISPER_MODEL = "base"  # tiny / base / small - bigger is more accurate but slower
# Voice: download once with   python -m piper.download_voices en_US-amy-medium --download-dir voices
PIPER_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices", "en_US-amy-medium.onnx")
VOICE_PITCH = 1.3       # 1.0 = normal, higher = squeakier and faster
HAS_PIPER = sd is not None and os.path.exists(PIPER_MODEL)
CAN_SPEAK = HAS_PIPER or pyttsx3 is not None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SPRITE_DIR = os.path.join(BASE_DIR, "sprites")


CONFIG_PATH = os.path.join(BASE_DIR, "buddy_config.json")


def lighten(color, amount=0.12):
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    r = int(r + (255 - r) * amount)
    g = int(g + (255 - g) * amount)
    b = int(b + (255 - b) * amount)
    return "#%02x%02x%02x" % (r, g, b)


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class Card(tk.Canvas):
    """A rounded panel. Put child widgets inside card.inner."""

    def __init__(self, parent, fill, outer=None, radius=20, pad=12, **kw):
        tk.Canvas.__init__(self, parent, bg=outer or C["bg"], highlightthickness=0, bd=0, **kw)
        self.fill, self.radius, self.pad, self.shape = fill, radius, pad, None
        self.inner = tk.Frame(self, bg=fill)
        self.win = self.create_window(pad, pad, window=self.inner, anchor="nw")
        self.bind("<Configure>", self.redraw)

    def redraw(self, e):
        if self.shape:
            self.delete(self.shape)
        self.shape = round_rect(self, 0, 0, e.width, e.height, self.radius, fill=self.fill, outline="")
        self.tag_lower(self.shape)
        self.itemconfigure(self.win, width=max(1, e.width - 2 * self.pad),
                           height=max(1, e.height - 2 * self.pad))


class RoundButton(tk.Canvas):
    """A pill-shaped button with a hover effect."""

    def __init__(self, parent, text, command, bg, fg, outer, height=40,
                 font=("Segoe UI", 10, "bold"), padx=20):
        width = tkfont.Font(font=font).measure(text) + 2 * padx
        tk.Canvas.__init__(self, parent, width=width, height=height, bg=outer,
                           highlightthickness=0, bd=0, cursor="hand2")
        self.command, self.bg, self.fg, self.text = command, bg, fg, text
        self.btn_font, self.state, self.hover = font, "normal", False
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Enter>", lambda e: self.set_hover(True))
        self.bind("<Leave>", lambda e: self.set_hover(False))
        self.bind("<Button-1>", self.on_click)

    def set_hover(self, value):
        self.hover = value
        self.draw()

    def on_click(self, event):
        if self.state == "normal" and self.command:
            self.command()

    def config(self, **kw):             # same option names as tk.Button
        if "text" in kw:
            self.text = kw["text"]
        if "bg" in kw:
            self.bg = kw["bg"]
        if "fg" in kw:
            self.fg = kw["fg"]
        if "state" in kw:
            self.state = kw["state"]
        self.draw()

    configure = config

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            w, h = int(self.cget("width")), int(self.cget("height"))
        fill, text_color = self.bg, self.fg
        if self.state != "normal":
            fill, text_color = C["surface2"], C["muted"]
        elif self.hover:
            fill = lighten(self.bg)
        round_rect(self, 0, 0, w, h, h // 2, fill=fill, outline="")
        self.create_text(w // 2, h // 2, text=self.text, fill=text_color, font=self.btn_font)


class Bubble(tk.Canvas):
    """A rounded chat bubble that grows with its text (also while streaming)."""

    def __init__(self, parent, text, fill, fg, wrap):
        tk.Canvas.__init__(self, parent, bg=C["surface"], highlightthickness=0, bd=0)
        self.fill, self.shape = fill, None
        self.label = tk.Label(self, text=text, bg=fill, fg=fg, font=("Segoe UI", 11),
                              justify="left", wraplength=wrap)
        self.create_window(15, 10, window=self.label, anchor="nw")
        self.set_text(text)

    def set_text(self, text):
        self.label.config(text=text)
        self.label.update_idletasks()
        w = self.label.winfo_reqwidth() + 30
        h = self.label.winfo_reqheight() + 20
        self.config(width=w, height=h)
        if self.shape:
            self.delete(self.shape)
        self.shape = round_rect(self, 0, 0, w, h, 18, fill=self.fill, outline="")
        self.tag_lower(self.shape)


class App:
    def __init__(self, root):
        self.root = root
        root.title("Local Buddy")
        root.geometry("1000x640")
        root.minsize(880, 620)

        self.q = queue.Queue()
        self.history = []
        self.busy = False
        self.state = "idle"
        self.frame_index = 0
        self.sent_at = 0.0
        self.sprites = {}            # state -> list of PhotoImage
        self.count_labels = {}
        self.speak_var = tk.BooleanVar(value=False)
        self.mic_var = tk.BooleanVar(value=False)
        self.threshold_var = tk.DoubleVar(value=0.02)
        self.audio_q = queue.Queue()
        self.preroll = deque(maxlen=3)   # keeps the last 0.3s so the first syllable isn't cut
        self.rec_buf = []
        self.hearing = False
        self.last_voice = 0.0
        self.stream = None
        self.whisper = None

        self.bubbles = []
        self.cur_bubble = None
        self.generating = False
        self.load_config()
        self.build_ui()
        for name in STATES:
            self.load_sprites(name)
        self.refresh_models()
        self.show_frame()
        self.root.update_idletasks()
        self.add_message("Hi! I'm ready to chat. What's on your mind?", "bot")
        self.animate()
        self.poll()

    # ---------------- UI ----------------
    def flat_button(self, parent, text, command, bg, fg=None, outer=None, height=40):
        return RoundButton(parent, text, command, bg, fg or C["text"], outer or C["bg"], height=height)

    def style_ttk(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TCombobox", fieldbackground=C["surface2"], background=C["surface2"],
                     foreground=C["text"], arrowcolor=C["text"], bordercolor=C["surface2"],
                     lightcolor=C["surface2"], darkcolor=C["surface2"], padding=6)
        st.map("TCombobox", fieldbackground=[("readonly", C["surface2"])],
               foreground=[("readonly", C["text"])])
        st.configure("Horizontal.TScale", background=C["bg"], troughcolor=C["surface2"])
        self.root.option_add("*TCombobox*Listbox.background", C["surface2"])
        self.root.option_add("*TCombobox*Listbox.foreground", C["text"])

    def build_ui(self):
        root = self.root
        root.configure(bg=C["bg"])
        self.style_ttk()

        # header
        header = tk.Frame(root, bg=C["bg"])
        header.pack(fill="x", padx=22, pady=(18, 0))
        tk.Label(header, text="Local Buddy", bg=C["bg"], fg=C["text"],
                 font=("Segoe UI", 18, "bold")).pack(side="left")
        self.flat_button(header, "⚙  Settings", self.open_settings, C["surface2"], height=38).pack(side="right")
        self.flat_button(header, "✨  Personality", self.open_persona, C["accent"],
                         C["accent_text"], height=38).pack(side="right", padx=10)

        body = tk.Frame(root, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=22, pady=18)

        # left: character card
        left = Card(body, C["surface"], pad=20, width=STAGE_SIZE + 60)
        left.pack(side="left", fill="y", padx=(0, 18))
        li = left.inner
        stage_card = Card(li, C["surface2"], outer=C["surface"], radius=24, pad=10,
                          width=STAGE_SIZE + 20, height=STAGE_SIZE + 20)
        stage_card.pack(pady=(4, 14))
        self.stage = tk.Label(stage_card.inner, font=("Segoe UI Emoji", 80), bg=C["surface2"], fg=C["text"])
        self.stage.pack(fill="both", expand=True)
        self.persona_label = tk.Label(li, text=self.persona_name, bg=C["surface"], fg=C["text"],
                                      font=("Segoe UI", 15, "bold"))
        self.persona_label.pack()
        row = tk.Frame(li, bg=C["surface"])
        row.pack(pady=(4, 0))
        self.status_dot = tk.Label(row, text="●", bg=C["surface"], fg=STATE_COLORS["idle"], font=("Segoe UI", 11))
        self.status_dot.pack(side="left")
        self.status = tk.Label(row, text="idle", bg=C["surface"], fg=C["text"], font=("Segoe UI", 10, "bold"))
        self.status.pack(side="left", padx=(5, 0))
        self.hint = tk.Label(li, text=HINTS["idle"], bg=C["surface"], fg=C["muted"], font=("Segoe UI", 9))
        self.hint.pack()
        self.mic_btn = self.flat_button(li, "🎤  Mic off", self.mic_click, C["surface2"], outer=C["surface"])
        self.mic_btn.pack(fill="x", pady=(16, 4))
        if not sd:
            self.mic_btn.config(state="disabled", text="Mic unavailable")

        # right: chat
        right = tk.Frame(body, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True)
        chat_card = Card(right, C["surface"], pad=14)
        chat_card.pack(fill="both", expand=True)
        self.chat = tk.Text(chat_card.inner, wrap="word", state="disabled", bd=0, highlightthickness=0,
                            bg=C["surface"], fg=C["text"], padx=6, pady=6, cursor="arrow",
                            font=("Segoe UI", 11))
        self.chat.pack(fill="both", expand=True)
        self.chat.tag_config("right", justify="right", spacing1=5, spacing3=5, rmargin=4)
        self.chat.tag_config("left", justify="left", spacing1=5, spacing3=5, lmargin1=4)
        self.chat.tag_config("note", foreground=C["muted"], justify="center", font=("Segoe UI", 9, "italic"),
                             spacing1=6, spacing3=6)

        bottom = tk.Frame(right, bg=C["bg"])
        bottom.pack(fill="x", pady=(14, 0))
        pill = Card(bottom, C["surface"], radius=26, pad=14, height=52)
        pill.pack(side="left", fill="x", expand=True)
        self.entry = tk.Entry(pill.inner, font=("Segoe UI", 12), relief="flat", bd=0, highlightthickness=0,
                              bg=C["surface"], fg=C["text"], insertbackground=C["text"])
        self.entry.pack(fill="both", expand=True)
        self.entry.bind("<Return>", lambda e: self.send())
        self.send_btn = self.flat_button(bottom, "Send  ➤", self.send, C["accent"], C["accent_text"], height=52)
        self.send_btn.pack(side="left", padx=(10, 0))
        self.entry.focus()

        self.build_settings()
        self.build_persona()

    def heading(self, win, text):
        tk.Label(win, text=text, bg=C["bg"], fg=C["text"],
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=22, pady=(16, 5))

    def make_window(self, title):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=C["bg"])
        win.protocol("WM_DELETE_WINDOW", win.withdraw)   # closing just hides it
        win.withdraw()
        return win

    def build_settings(self):
        win = self.make_window("Settings")
        win.resizable(False, False)
        self.settings = win

        self.heading(win, "Sprites  (PNG / GIF - several images = animation)")
        for name in STATES:
            row = tk.Frame(win, bg=C["bg"])
            row.pack(fill="x", padx=22, pady=3)
            tk.Label(row, text=name, width=11, anchor="w", bg=C["bg"], fg=C["text"]).pack(side="left")
            lbl = tk.Label(row, text="0 img", width=7, anchor="w", bg=C["bg"], fg=C["muted"])
            lbl.pack(side="left")
            self.count_labels[name] = lbl
            self.flat_button(row, "Upload", lambda s=name: self.upload(s), C["surface2"], height=30).pack(side="left", padx=3)
            self.flat_button(row, "Remove", lambda s=name: self.clear_sprites(s), C["surface2"], height=30).pack(side="left")

        self.heading(win, "Model")
        self.model_box = ttk.Combobox(win, state="readonly", width=34)
        self.model_box.pack(anchor="w", padx=22)
        self.flat_button(win, "Refresh models", self.refresh_models, C["surface2"], height=30).pack(anchor="w", padx=22, pady=6)

        self.heading(win, "Voice")
        if CAN_SPEAK:
            tk.Checkbutton(win, text="Speak replies aloud", variable=self.speak_var, bg=C["bg"], fg=C["text"],
                           selectcolor=C["surface2"], activebackground=C["bg"],
                           activeforeground=C["text"]).pack(anchor="w", padx=18)
        if sd:
            tk.Label(win, text="Mic threshold (lower = more sensitive)", bg=C["bg"], fg=C["muted"]).pack(anchor="w", padx=22, pady=(8, 0))
            ttk.Scale(win, from_=0.005, to=0.1, variable=self.threshold_var, length=260).pack(anchor="w", padx=22)
        else:
            tk.Label(win, text="Mic off: pip install sounddevice numpy", bg=C["bg"], fg=C["muted"]).pack(anchor="w", padx=22)

        self.heading(win, "Chat")
        self.flat_button(win, "Clear chat", self.clear_chat, C["surface2"], height=30).pack(anchor="w", padx=22, pady=(0, 20))

    def build_persona(self):
        win = self.make_window("Personality")
        win.resizable(False, False)
        self.persona_win = win

        self.heading(win, "Describe a personality")
        row = tk.Frame(win, bg=C["bg"])
        row.pack(fill="x", padx=22)
        pill = Card(row, C["surface"], radius=22, pad=14, height=46)
        pill.pack(side="left", fill="x", expand=True)
        self.persona_entry = tk.Entry(pill.inner, font=("Segoe UI", 11), relief="flat", bd=0, highlightthickness=0,
                                      bg=C["surface"], fg=C["text"], insertbackground=C["text"])
        self.persona_entry.pack(fill="both", expand=True)
        self.persona_entry.bind("<Return>", lambda e: self.generate_persona())
        self.flat_button(row, "Generate & apply", self.generate_persona, C["accent"],
                         C["accent_text"], height=46).pack(side="left", padx=(10, 0))
        self.persona_status = tk.Label(win, text="e.g.  a grumpy pirate who loves maths", bg=C["bg"],
                                       fg=C["muted"], font=("Segoe UI", 9), anchor="w")
        self.persona_status.pack(fill="x", padx=24, pady=(6, 0))

        self.heading(win, "Presets")
        row2 = tk.Frame(win, bg=C["bg"])
        row2.pack(fill="x", padx=22)
        self.preset_box = ttk.Combobox(row2, state="readonly", width=30, values=sorted(self.personas))
        self.preset_box.pack(side="left")
        if self.persona_name in self.personas:
            self.preset_box.set(self.persona_name)
        self.flat_button(row2, "Use preset", self.use_preset, C["surface2"], height=34).pack(side="left", padx=10)

        self.heading(win, "Current personality (you can edit it)")
        self.persona_text = tk.Text(win, width=62, height=9, wrap="word", bd=0, highlightthickness=0,
                                    bg=C["surface"], fg=C["text"], insertbackground=C["text"],
                                    padx=14, pady=12, font=("Segoe UI", 10))
        self.persona_text.pack(padx=22)
        self.persona_text.insert("1.0", self.system_prompt)
        row3 = tk.Frame(win, bg=C["bg"])
        row3.pack(fill="x", padx=22, pady=(12, 20))
        self.flat_button(row3, "Apply edits", self.apply_edits, C["accent"], C["accent_text"], height=36).pack(side="left")
        self.flat_button(row3, "Save as preset", self.save_preset, C["surface2"], height=36).pack(side="left", padx=10)

    def open_settings(self):
        self.settings.deiconify()
        self.settings.lift()

    def open_persona(self):
        self.persona_win.deiconify()
        self.persona_win.lift()

    # ---------------- personality ----------------
    def load_config(self):
        self.personas = dict(DEFAULT_PERSONAS)
        self.persona_name = "Doraemon"
        self.system_prompt = self.personas["Doraemon"]
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            custom = data.get("custom_personas", {})
            for key in custom:
                self.personas[key] = custom[key]
            self.persona_name = data.get("current_name", self.persona_name)
            self.system_prompt = data.get("current_prompt") or self.personas.get(self.persona_name, self.system_prompt)
        except (OSError, ValueError):
            pass    # first run or unreadable file: use the defaults

    def save_config(self):
        custom = {}
        for key in self.personas:
            if key not in DEFAULT_PERSONAS:
                custom[key] = self.personas[key]
        data = {"custom_personas": custom, "current_name": self.persona_name,
                "current_prompt": self.system_prompt}
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError:
            pass

    def apply_persona(self, name, prompt):
        prompt = prompt.strip()
        if not prompt:
            return
        self.persona_text.delete("1.0", "end")
        self.persona_text.insert("1.0", prompt)
        if self.busy:
            self.persona_status.config(text="Wait for the current reply to finish, then press Apply edits.")
            return
        self.persona_name, self.system_prompt = name, prompt
        self.persona_label.config(text=name)
        if name in self.personas:
            self.preset_box.set(name)
        self.clear_chat()                       # a fresh start so the old persona doesn't leak in
        self.add_text("Now chatting as " + name + "\n", "note")
        self.add_message("Hi! What's on your mind?", "bot")
        self.save_config()

    def use_preset(self):
        name = self.preset_box.get()
        if name in self.personas:
            self.apply_persona(name, self.personas[name])
            self.persona_status.config(text="Switched to " + name)

    def apply_edits(self):
        self.apply_persona(self.persona_name, self.persona_text.get("1.0", "end"))

    def save_preset(self):
        text = self.persona_text.get("1.0", "end").strip()
        name = simpledialog.askstring("Save preset", "Name this personality:",
                                      initialvalue=self.persona_name, parent=self.persona_win)
        if not name or not text:
            return
        self.personas[name] = text
        self.preset_box["values"] = sorted(self.personas)
        self.preset_box.set(name)
        self.persona_name, self.system_prompt = name, text
        self.persona_label.config(text=name)
        self.save_config()
        self.persona_status.config(text="Saved preset: " + name)

    def generate_persona(self):
        desc = self.persona_entry.get().strip()
        if not desc or self.generating:
            return
        model = self.model_box.get()
        if not model:
            self.persona_status.config(text="Pick a model first (is Ollama running?).")
            return
        self.generating = True
        self.persona_status.config(text="Writing personality...")
        threading.Thread(target=self.persona_worker, args=(model, desc), daemon=True).start()

    def persona_worker(self, model, desc):
        """Background thread: the local model writes the system prompt from one sentence."""
        ask = ("Write a system prompt (3 to 6 sentences) for an AI chat character described as: " + desc +
               ". Write it in second person ('You are ...'). Say how the character talks and what it "
               "cares about, and that replies must stay short (1-3 sentences) with no emojis or markdown "
               "because they are spoken aloud. Output only the prompt text.")
        try:
            r = requests.post(OLLAMA_URL + "/api/chat", timeout=180,
                              json={"model": model, "stream": False,
                                    "messages": [{"role": "user", "content": ask}]})
            r.raise_for_status()
            self.q.put(("persona_ready", (desc, r.json()["message"]["content"].strip())))
        except Exception as e:
            self.q.put(("persona_error", str(e)))

    # ---------------- mic button ----------------
    def mic_click(self):
        self.mic_var.set(not self.mic_var.get())
        self.toggle_mic()
        self.update_mic_button()

    def update_mic_button(self):
        if self.mic_var.get():
            self.mic_btn.config(text="🎤  Mic on - listening", bg="#10b981", fg="#04261a")
        else:
            self.mic_btn.config(text="🎤  Mic off", bg=C["surface2"], fg=C["text"])

    # ---------------- chat helpers ----------------
    def add_text(self, text, tag):
        self.chat.config(state="normal")
        self.chat.insert("end-1c", text, tag)
        self.chat.config(state="disabled")
        self.chat.see("end")

    def add_message(self, text, who):
        wrap = max(240, int(self.chat.winfo_width() * 0.62))
        if who == "me":
            bubble, tag = Bubble(self.chat, text, C["me"], C["me_text"], wrap), "right"
        else:
            bubble, tag = Bubble(self.chat, text, C["bot"], C["text"], wrap), "left"
        self.bubbles.append(bubble)
        for widget in (bubble, bubble.label):
            widget.bind("<MouseWheel>", self.wheel)
        self.chat.config(state="normal")
        start = self.chat.index("end-1c")
        self.chat.window_create("end-1c", window=bubble)
        self.chat.insert("end-1c", "\n")
        self.chat.tag_add(tag, start, "end-1c")
        self.chat.config(state="disabled")
        self.chat.see("end")
        return bubble

    def wheel(self, event):
        self.chat.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def clear_chat(self):
        self.history = []
        self.chat.config(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.config(state="disabled")
        for bubble in self.bubbles:
            bubble.destroy()
        self.bubbles = []

    # ---------------- sprites ----------------
    def sprite_folder(self, name):
        return os.path.join(SPRITE_DIR, name)

    def load_sprites(self, name):
        frames = []
        folder = self.sprite_folder(name)
        if os.path.isdir(folder):
            for fname in sorted(os.listdir(folder)):
                path = os.path.join(folder, fname)
                try:
                    img = Image.open(path)
                    for fr in ImageSequence.Iterator(img):   # works for GIF and single images
                        fr = fr.convert("RGBA")
                        fr.thumbnail((STAGE_SIZE, STAGE_SIZE))
                        frames.append(ImageTk.PhotoImage(fr))
                except Exception:
                    pass  # skip files that aren't images
        self.sprites[name] = frames
        self.count_labels[name].config(text=str(len(frames)) + " img")

    def upload(self, name):
        paths = filedialog.askopenfilenames(
            title="Pick sprite image(s) for '" + name + "'",
            filetypes=[("Images", "*.png *.gif *.jpg *.jpeg *.webp"), ("All files", "*.*")])
        if not paths:
            return
        self.clear_sprites(name, reload=False)
        folder = self.sprite_folder(name)
        os.makedirs(folder, exist_ok=True)
        n = 0
        for p in paths:
            ext = os.path.splitext(p)[1].lower()
            shutil.copy(p, os.path.join(folder, "%03d%s" % (n, ext)))
            n = n + 1
        self.load_sprites(name)
        self.frame_index = 0
        self.show_frame()

    def clear_sprites(self, name, reload=True):
        folder = self.sprite_folder(name)
        if os.path.isdir(folder):
            shutil.rmtree(folder)
        if reload:
            self.load_sprites(name)
            self.show_frame()

    def show_frame(self):
        frames = self.sprites.get(self.state, [])
        if frames:
            self.stage.config(image=frames[self.frame_index % len(frames)], text="")
        else:
            self.stage.config(image="", text=FALLBACK[self.state])

    def animate(self):
        if len(self.sprites.get(self.state, [])) > 1:
            self.frame_index = self.frame_index + 1
            self.show_frame()
        self.root.after(FRAME_MS, self.animate)

    def set_state(self, s):
        if s == self.state:
            return
        self.state = s
        self.frame_index = 0
        self.status.config(text=s)
        self.status_dot.config(fg=STATE_COLORS[s])
        self.hint.config(text=HINTS[s])
        self.show_frame()

    # ---------------- Ollama ----------------
    def refresh_models(self):
        try:
            data = requests.get(OLLAMA_URL + "/api/tags", timeout=5).json()
            names = []
            for m in data.get("models", []):
                names.append(m["name"])
            self.model_box["values"] = names
            if names:
                if not self.model_box.get():
                    self.model_box.set(names[0])
            else:
                self.add_text("Ollama has no models yet. Run: ollama pull llama3.2\n\n", "note")
        except Exception:
            self.add_text("Can't reach Ollama at " + OLLAMA_URL + ". Is it running?\n\n", "note")

    def send(self):
        text = self.entry.get().strip()
        if not text or self.busy:
            return
        self.entry.delete(0, "end")
        self.send_text(text)

    def send_text(self, text):
        model = self.model_box.get()
        if not model:
            self.add_text("Pick a model first (is Ollama running?).\n\n", "note")
            self.finish()
            return
        self.busy = True
        self.send_btn.config(state="disabled")
        self.first_token = True
        self.reply = ""

        self.history.append({"role": "user", "content": text})
        self.add_message(text, "me")
        self.cur_bubble = self.add_message("···", "bot")

        messages = [{"role": "system", "content": self.system_prompt}]
        start = max(0, len(self.history) - MAX_TURNS)
        messages = messages + self.history[start:]

        self.sent_at = time.time()
        self.set_state("thinking")
        threading.Thread(target=self.stream_reply, args=(model, messages), daemon=True).start()

    def stream_reply(self, model, messages):
        """Runs in a background thread; talks to the UI only through the queue."""
        try:
            r = requests.post(OLLAMA_URL + "/api/chat",
                              json={"model": model, "messages": messages, "stream": True},
                              stream=True, timeout=300)
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                obj = json.loads(line)
                piece = obj.get("message", {}).get("content", "")
                if piece:
                    self.q.put(("token", piece))
            self.q.put(("done", None))
        except Exception as e:
            self.q.put(("error", str(e)))

    def speak(self, text):
        def run():
            try:
                if HAS_PIPER:
                    self.speak_piper(text)
                else:
                    engine = pyttsx3.init()
                    engine.say(text)
                    engine.runAndWait()
            except Exception as e:
                print("speech error:", e)
            self.q.put(("spoken", None))
        threading.Thread(target=run, daemon=True).start()

    def speak_piper(self, text):
        """Neural voice from Piper (offline), then pitched up for a cartoon sound."""
        text = text.replace("*", "")
        out = os.path.join(BASE_DIR, "_speech.wav")
        subprocess.run([sys.executable, "-m", "piper", "-m", PIPER_MODEL, "-f", out],
                       input=text.encode("utf-8"), check=True, capture_output=True)
        with wave.open(out, "rb") as w:
            rate = w.getframerate()
            raw = w.readframes(w.getnframes())
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        n = len(data)
        new_n = int(n / VOICE_PITCH)               # fewer samples = higher pitch
        data = np.interp(np.linspace(0, n - 1, new_n), np.arange(n), data)
        sd.play(data.astype(np.float32), rate)
        sd.wait()

    def finish(self):
        self.set_state("idle")
        self.busy = False
        self.send_btn.config(state="normal")
        self.entry.focus()

    # ---------------- microphone ----------------
    def toggle_mic(self):
        if self.mic_var.get():
            try:
                self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                             blocksize=SAMPLE_RATE // 10, callback=self.on_audio)
                self.stream.start()
            except Exception as e:
                self.mic_var.set(False)
                self.add_text("Couldn't open the microphone: " + str(e) + "\n\n", "note")
                return
            if not WhisperModel:
                self.add_text("Mic on: Buddy will react to sound. To also understand speech, "
                              "run: pip install faster-whisper\n\n", "note")
        else:
            if self.stream:
                self.stream.stop()
                self.stream.close()
                self.stream = None
            self.hearing = False
            self.rec_buf = []
            if not self.busy:
                self.set_state("idle")

    def on_audio(self, indata, frames, t, status):
        self.audio_q.put(indata[:, 0].copy())   # audio thread: just hand the data over

    def process_audio(self):
        while not self.audio_q.empty():
            chunk = self.audio_q.get()
            if self.busy or not self.mic_var.get():   # don't listen to myself while replying
                self.hearing = False
                self.rec_buf = []
                continue
            level = float(np.sqrt(np.mean(chunk ** 2)))
            now = time.time()
            if level > self.threshold_var.get():
                if not self.hearing:
                    self.hearing = True
                    self.rec_buf = list(self.preroll)
                    self.set_state("listening")
                self.last_voice = now
                self.rec_buf.append(chunk)
            elif self.hearing:
                self.rec_buf.append(chunk)
                if now - self.last_voice > SILENCE_END:
                    self.end_utterance()
            else:
                self.preroll.append(chunk)

    def end_utterance(self):
        self.hearing = False
        audio = np.concatenate(self.rec_buf)
        self.rec_buf = []
        if len(audio) < MIN_SPEECH * SAMPLE_RATE or not WhisperModel:
            self.set_state("idle")
            return
        self.busy = True
        self.send_btn.config(state="disabled")
        self.sent_at = time.time()
        self.set_state("thinking")
        threading.Thread(target=self.transcribe, args=(audio,), daemon=True).start()

    def transcribe(self, audio):
        """Background thread: speech -> text, fully local."""
        try:
            if self.whisper is None:    # first use downloads the model once
                self.whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
            segments, info = self.whisper.transcribe(audio, beam_size=1)
            text = ""
            for seg in segments:
                text = text + seg.text
            self.q.put(("heard", text.strip()))
        except Exception as e:
            self.q.put(("stt_error", str(e)))

    # ---------------- main-thread event loop ----------------
    def poll(self):
        self.process_audio()
        while not self.q.empty():
            kind, data = self.q.get()
            if kind == "token":
                if self.first_token:
                    self.first_token = False
                    self.set_state("talking")
                self.reply = self.reply + data
                self.cur_bubble.set_text(self.reply)
                self.chat.see("end")
            elif kind == "done":
                self.history.append({"role": "assistant", "content": self.reply})
                if CAN_SPEAK and self.speak_var.get() and self.reply:
                    self.speak(self.reply)      # stays in "talking" until spoken
                else:
                    self.finish()
            elif kind == "spoken":
                self.finish()
            elif kind == "heard":
                self.busy = False
                if data:
                    self.send_text(data)    # your spoken words go to the LLM
                else:
                    self.finish()
            elif kind == "persona_ready":
                self.generating = False
                desc, text = data
                self.persona_status.config(text="Applied.")
                self.apply_persona(desc[:1].upper() + desc[1:28], text)
            elif kind == "persona_error":
                self.generating = False
                self.persona_status.config(text="Couldn't write it: " + data)
            elif kind == "stt_error":
                self.add_text("[speech error] " + data + "\n\n", "note")
                self.finish()
            elif kind == "error":
                if not self.reply:
                    self.cur_bubble.set_text("(no reply)")
                self.add_text("[error] " + data + "\n", "note")
                self.history.pop()              # drop the failed user turn
                self.finish()

        # thinking has gone on a while -> processing
        if self.busy and self.state == "thinking" and time.time() - self.sent_at > SLOW_AFTER:
            self.set_state("processing")

        self.root.after(50, self.poll)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
