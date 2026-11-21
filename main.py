"""
WinTAL_scan - Parla con il tuo PC.
Interfaccia moderna "glossy" (customtkinter) sopra il motore wintal_core.
"""
import os
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont

try:
    import customtkinter as ctk
except ImportError:
    print("Installa le dipendenze:  pip install -r requirements.txt")
    sys.exit(1)

from PIL import Image, ImageDraw, ImageFilter, ImageTk

import wintal_core as core

APP_NAME = "WinTAL_scan"
VERSION = "2.0"

# ===================== TEMA =====================
C = {
    "bg": "#0A0F1E",
    "panel": "#121A31",
    "panel2": "#18223F",
    "border": "#26345C",
    "text": "#E8EEFF",
    "muted": "#8A97BD",
    "blue": "#2F7BFF",
    "blue_hover": "#4D90FF",
    "cyan": "#22D3EE",
    "violet": "#8B5CF6",
    "green": "#22C55E",
    "orange": "#F59E0B",
    "red": "#EF4444",
    "user_bubble": "#2563EB",
    "bot_bubble": "#16203D",
}


def pick_font():
    try:
        fams = set(tkfont.families())
    except Exception:
        fams = set()
    for f in ("Segoe UI Variable Display", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans"):
        if f in fams:
            return f
    return "TkDefaultFont"


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def load_logo():
    for name in ("logo_glossy.png", "logo_new.jpg", "logo.jpg"):
        p = core.resource_path(name)
        if os.path.exists(p):
            return Image.open(p).convert("RGBA")
    return None


def render_header(w, h, logo):
    """Banner glossy: gradiente blu -> ciano, riflesso lucido e bagliore."""
    w = max(w, 200)
    img = Image.new("RGB", (w, h))
    px = img.load()
    c0, c1, c2 = hex2rgb("#0B1A4A"), hex2rgb("#1D4ED8"), hex2rgb("#06B6D4")
    for x in range(w):
        t = x / (w - 1)
        col = lerp(c0, c1, t / 0.55) if t < 0.55 else lerp(c1, c2, (t - 0.55) / 0.45)
        for y in range(h):
            k = 1 - 0.25 * (y / h)
            px[x, y] = tuple(int(v * k) for v in col)
    img = img.convert("RGBA")
    over = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    # bagliori morbidi
    d.ellipse((int(w * 0.62), -h, int(w * 1.1), int(h * 1.4)), fill=(34, 211, 238, 70))
    d.ellipse((-int(h * 0.8), -h // 2, int(h * 2.2), int(h * 1.6)), fill=(139, 92, 246, 55))
    over = over.filter(ImageFilter.GaussianBlur(40))
    img.alpha_composite(over)
    # riflesso lucido nella metà alta
    gloss = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gloss)
    for y in range(h // 2):
        a = int(46 * (1 - y / (h / 2)))
        gd.line([(0, y), (w, y)], fill=(255, 255, 255, a))
    img.alpha_composite(gloss)
    # linea luminosa in basso
    ImageDraw.Draw(img).line([(0, h - 1), (w, h - 1)], fill=(120, 220, 255, 160))
    if logo:
        size = h - 18
        lg = logo.copy()
        lg.thumbnail((size, size), Image.LANCZOS)
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        glow.paste((150, 230, 255, 110), (18, 9), lg)
        glow = glow.filter(ImageFilter.GaussianBlur(10))
        img.alpha_composite(glow)
        img.alpha_composite(lg, (18, (h - lg.height) // 2))
    return img


# ===================== WIDGET =====================
class StatCard(ctk.CTkFrame):
    def __init__(self, master, title, color, font):
        super().__init__(master, fg_color=C["panel"], corner_radius=16,
                         border_width=1, border_color=C["border"])
        self.color = color
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(top, text="●", text_color=color, font=(font, 12)).pack(side="left")
        ctk.CTkLabel(top, text=title.upper(), text_color=C["muted"],
                     font=(font, 11, "bold")).pack(side="left", padx=6)
        self.value = ctk.CTkLabel(self, text="--", text_color=C["text"], font=(font, 22, "bold"), anchor="w")
        self.value.pack(fill="x", padx=14)
        self.sub = ctk.CTkLabel(self, text=" ", text_color=C["muted"], font=(font, 11), anchor="w")
        self.sub.pack(fill="x", padx=14)
        self.bar = ctk.CTkProgressBar(self, height=6, corner_radius=3, progress_color=color,
                                      fg_color=C["panel2"])
        self.bar.pack(fill="x", padx=14, pady=(4, 12))
        self.bar.set(0)

    def update_value(self, value, sub, frac):
        self.value.configure(text=value)
        self.sub.configure(text=sub)
        frac = max(0.0, min(1.0, frac))
        self.bar.set(frac)
        self.bar.configure(progress_color=C["red"] if frac > 0.9 else self.color)


class Bubble(ctk.CTkFrame):
    def __init__(self, master, text, role, font, wrap):
        super().__init__(master, fg_color="transparent")
        user = role == "user"
        if not user:
            ctk.CTkLabel(self, text="WinTAL", text_color=C["cyan"],
                         font=(font, 11, "bold")).pack(anchor="w", padx=(6, 0))
        self.box = ctk.CTkFrame(self, corner_radius=18,
                                fg_color=C["user_bubble"] if user else C["bot_bubble"],
                                border_width=0 if user else 1, border_color=C["border"])
        self.box.pack(anchor="e" if user else "w")
        mono = role == "bot" and text.count("\n") > 4 and (" - " in text or "\\" in text)
        self.label = ctk.CTkLabel(self.box, text=text, justify="left", anchor="w", wraplength=wrap,
                                  text_color="#FFFFFF" if user else C["text"],
                                  font=("Consolas", 12) if mono else (font, 14))
        self.label.pack(padx=16, pady=10)
        for w in (self.label, self.box):
            w.bind("<Button-3>", self._menu)
        self.text = text

    def set_wrap(self, wrap):
        self.label.configure(wraplength=wrap)

    def _menu(self, e):
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="Copia testo", command=lambda: (self.clipboard_clear(), self.clipboard_append(self.text)))
        m.tk_popup(e.x_root, e.y_root)


class ConfirmDialog(ctk.CTkToplevel):
    def __init__(self, master, title, detail, font):
        super().__init__(master)
        self.result = False
        self.title("Conferma azione")
        self.configure(fg_color=C["panel"])
        self.resizable(False, False)
        self.transient(master)
        ctk.CTkLabel(self, text="⚠", text_color=C["orange"], font=(font, 34)).pack(pady=(18, 0))
        ctk.CTkLabel(self, text=title, text_color=C["text"], font=(font, 16, "bold"),
                     wraplength=380).pack(padx=24, pady=(4, 2))
        if detail:
            ctk.CTkLabel(self, text=detail, text_color=C["muted"], font=(font, 12),
                         wraplength=380, justify="center").pack(padx=24)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(pady=18)
        ctk.CTkButton(row, text="Annulla", width=120, height=38, corner_radius=19, fg_color=C["panel2"],
                      hover_color=C["border"], font=(font, 13), command=self.destroy).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Procedi", width=120, height=38, corner_radius=19, fg_color=C["orange"],
                      hover_color="#FBBF24", text_color="#1A1200", font=(font, 13, "bold"),
                      command=self._ok).pack(side="left", padx=6)
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{x}+{y}")
        self.after(50, self.grab_set)

    def _ok(self):
        self.result = True
        self.destroy()


class MonitorWidget(tk.Toplevel):
    """Mini monitor sempre in primo piano, trascinabile. Doppio clic = apri WinTAL."""
    KEY = "#010203"

    def __init__(self, app, sensors, font):
        super().__init__(app)
        self.app, self.sensors = app, sensors
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=self.KEY)
        if core.IS_WINDOWS:
            self.attributes("-transparentcolor", self.KEY)  # angoli arrotondati veri
            self.attributes("-alpha", 0.96)
        w, h = 250, 122
        self.geometry(f"{w}x{h}+{self.winfo_screenwidth() - w - 20}+{self.winfo_screenheight() - h - 70}")
        card = ctk.CTkFrame(self, fg_color=C["panel"], bg_color=self.KEY, corner_radius=18,
                            border_width=1, border_color=C["border"])
        card.pack(fill="both", expand=True)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(head, text="● WinTAL", text_color=C["cyan"], font=(font, 12, "bold")).pack(side="left")
        self.temp = ctk.CTkLabel(head, text="", text_color=C["muted"], font=(font, 11))
        self.temp.pack(side="right")
        self.rows = {}
        for key, label, color in (("cpu", "CPU", C["cyan"]), ("ram", "RAM", C["violet"]), ("disk", "C:", C["blue"])):
            r = ctk.CTkFrame(card, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=1)
            ctk.CTkLabel(r, text=label, width=34, height=20, anchor="w", text_color=C["muted"], font=(font, 11)).pack(side="left")
            val = ctk.CTkLabel(r, text="--", width=40, height=20, anchor="e", text_color=C["text"],
                               font=(font, 11, "bold"))
            val.pack(side="right")
            bar = ctk.CTkProgressBar(r, height=6, progress_color=color, fg_color=C["panel2"])
            bar.pack(side="left", fill="x", expand=True, padx=6)
            self.rows[key] = (bar, val, color)
        for wdg in [self, card, head] + list(card.winfo_children()):
            wdg.bind("<ButtonPress-1>", self._start)
            wdg.bind("<B1-Motion>", self._move)
            wdg.bind("<Double-Button-1>", lambda e: self.app.bring_front())
        self._tick()

    def _start(self, e):
        self._dx, self._dy = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()

    def _move(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _tick(self):
        d = self.sensors.data
        for key, (bar, val, color) in self.rows.items():
            v = d.get(key, 0) or 0
            bar.set(v / 100)
            bar.configure(progress_color=C["red"] if v > 90 else color)
            val.configure(text=f"{v:.0f}%")
        t = d.get("temp")
        self.temp.configure(text=f"{t:.0f}°C" if t else "")
        self.after(1000, self._tick)


# ===================== APP =====================
class MainApp(ctk.CTk):
    QUICK = [
        ("Info sistema", "Dimmi tutto sul mio PC: sistema, CPU, RAM, dischi e scheda video"),
        ("Cosa rallenta il PC?", "Quali processi stanno usando più RAM e CPU adesso?"),
        ("File più pesanti", f"Trovami i file più pesanti in {core.HOME}"),
        ("Duplicati", f"Cerca i file duplicati in {core.HOME}"),
        ("Spazio recuperabile", "Quanto spazio posso liberare con una pulizia?"),
    ]

    def __init__(self):
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color=C["bg"])
        self.font = pick_font()
        self.cfg = core.load_config()
        self.title(f"{APP_NAME} — Parla con il tuo PC")
        self.geometry("1040x780")
        self.minsize(820, 620)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.history, self.bubbles, self.busy = [], [], False
        self.logo = load_logo()
        self._set_icon()

        self.sensors = core.SensorCache(self.cfg["update_interval"])
        self._build_header()
        self._build_stats()
        self._build_chat()
        self._build_input()
        self._build_footer()

        self.add_bubble("Ciao! Sono il tuo PC. 👋\nChiedimi come sto, cosa occupa spazio, quali file sono "
                        "duplicati o fammi fare una pulizia. Per le azioni che modificano qualcosa ti "
                        "chiederò sempre conferma.", "bot")
        self.monitor = MonitorWidget(self, self.sensors, self.font) if self.cfg.get("show_widget", True) else None
        self.after(500, self._refresh_stats)
        threading.Thread(target=self._check_ollama, daemon=True).start()

    # ---------- costruzione ----------
    def _set_icon(self):
        if not self.logo:
            return
        try:
            ico = self.logo.copy()
            ico.thumbnail((64, 64))
            self._icon = ImageTk.PhotoImage(ico)
            self.iconphoto(True, self._icon)
            if core.IS_WINDOWS and os.path.exists(core.resource_path("wintal.ico")):
                self.after(250, lambda: self.iconbitmap(core.resource_path("wintal.ico")))
        except Exception:
            pass

    def _build_header(self):
        self.header_h = 96
        self.header = tk.Canvas(self, height=self.header_h, bg=C["bg"], highlightthickness=0)
        self.header.pack(fill="x")
        self.header.bind("<Configure>", self._draw_header)
        self.status_text, self.status_color = "Controllo Ollama…", C["orange"]

    def _draw_header(self, e=None):
        w = self.header.winfo_width()
        if w < 50:
            return
        self._hdr_img = ImageTk.PhotoImage(render_header(w, self.header_h, self.logo))
        cv = self.header
        cv.delete("all")
        cv.create_image(0, 0, image=self._hdr_img, anchor="nw")
        x = 18 + self.header_h + 2
        cv.create_text(x + 1, 37, text="WinTAL", anchor="w", fill="#0A1A40", font=(self.font, 26, "bold"))
        cv.create_text(x, 36, text="WinTAL", anchor="w", fill="#FFFFFF", font=(self.font, 26, "bold"))
        tw = tkfont.Font(family=self.font, size=26, weight="bold").measure("WinTAL")
        cv.create_text(x + tw + 2, 38, text="_scan", anchor="w", fill="#A5F3FC", font=(self.font, 20, "bold"))
        cv.create_text(x, 66, text="Parla con il tuo PC  ·  AI locale, i tuoi dati non escono dal computer",
                       anchor="w", fill="#CFE3FF", font=(self.font, 12))
        # pillola di stato
        txt = f"●  {self.status_text}"
        f = tkfont.Font(family=self.font, size=11, weight="bold")
        pw = f.measure(txt) + 28
        x1, y1 = w - pw - 20, 30
        self._pill(cv, x1, y1, x1 + pw, y1 + 32, 16, "#0A1433")
        cv.create_text(x1 + 14, y1 + 16, text="●", anchor="w", fill=self.status_color, font=f)
        cv.create_text(x1 + 14 + f.measure("●  "), y1 + 16, text=self.status_text, anchor="w",
                       fill="#E8EEFF", font=f)

    @staticmethod
    def _pill(cv, x1, y1, x2, y2, r, fill):
        cv.create_oval(x1, y1, x1 + 2 * r, y2, fill=fill, outline="")
        cv.create_oval(x2 - 2 * r, y1, x2, y2, fill=fill, outline="")
        cv.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="")

    def set_status(self, text, color):
        self.status_text, self.status_color = text, color
        self._draw_header()

    def _build_stats(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(14, 6))
        self.cards = {}
        for i, (k, t, col) in enumerate((("cpu", "CPU", C["cyan"]), ("ram", "Memoria RAM", C["violet"]),
                                         ("disk", f"Disco {core.SYSTEM_DRIVE.rstrip(chr(92))}", C["blue"]),
                                         ("temp", "Temperatura", C["orange"]))):
            card = StatCard(row, t, col, self.font)
            card.grid(row=0, column=i, sticky="nsew", padx=6)
            row.grid_columnconfigure(i, weight=1, uniform="s")
            self.cards[k] = card

    def _refresh_stats(self):
        d = self.sensors.data
        vm_total = core.psutil.virtual_memory().total / 1024**3
        self.cards["cpu"].update_value(f"{d['cpu']:.0f}%", f"{core.psutil.cpu_count()} thread", d["cpu"] / 100)
        self.cards["ram"].update_value(f"{d['ram']:.0f}%", f"di {vm_total:.0f} GB", d["ram"] / 100)
        self.cards["disk"].update_value(f"{d['disk']:.0f}%", f"{d['disk_free']:.0f} GB liberi", d["disk"] / 100)
        t = d.get("temp")
        if t:
            self.cards["temp"].update_value(f"{t:.0f}°C", "CPU", t / 100)
        else:
            self.cards["temp"].update_value("n/d", "avvia come admin", 0)
        self.after(1500, self._refresh_stats)

    def _build_chat(self):
        wrap = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=20, border_width=1, border_color=C["border"])
        wrap.pack(fill="both", expand=True, padx=24, pady=8)
        self.chat = ctk.CTkScrollableFrame(wrap, fg_color="transparent", scrollbar_button_color=C["border"],
                                           scrollbar_button_hover_color=C["blue"])
        self.chat.pack(fill="both", expand=True, padx=6, pady=6)
        self.chat.bind("<Configure>", self._rewrap)
        self.typing = None

    def _wrap_width(self):
        return max(300, int(self.chat.winfo_width() * 0.72))

    def _rewrap(self, e=None):
        w = self._wrap_width()
        for b in self.bubbles:
            b.set_wrap(w)

    def add_bubble(self, text, role):
        b = Bubble(self.chat, text, role, self.font, self._wrap_width())
        b.pack(fill="x", padx=12, pady=6, anchor="e" if role == "user" else "w")
        self.bubbles.append(b)
        self._scroll_bottom()
        return b

    def _scroll_bottom(self):
        def go():
            try:
                cv = self.chat._parent_canvas
                cv.update_idletasks()
                cv.configure(scrollregion=cv.bbox("all"))
                cv.yview_moveto(1.0)
            except Exception:
                pass
        self.after(30, go)
        self.after(200, go)
        self.after(600, go)

    def _build_input(self):
        chips = ctk.CTkFrame(self, fg_color="transparent")
        chips.pack(fill="x", padx=24, pady=(4, 0))
        for label, prompt in self.QUICK:
            ctk.CTkButton(chips, text=label, width=20, height=30, corner_radius=15, fg_color=C["panel2"],
                          hover_color=C["border"], border_width=1, border_color=C["border"],
                          text_color=C["text"], font=(self.font, 12),
                          command=lambda p=prompt: self.send(p)).pack(side="left", padx=(0, 8))
        bar = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=28, border_width=1, border_color=C["border"])
        bar.pack(fill="x", padx=24, pady=10)
        self.entry = ctk.CTkEntry(bar, placeholder_text="Scrivi al tuo PC…  es. «cerca i file *.MOV sul desktop»",
                                  height=46, border_width=0, fg_color="transparent", text_color=C["text"],
                                  placeholder_text_color=C["muted"], font=(self.font, 14))
        self.entry.pack(side="left", fill="x", expand=True, padx=(18, 8), pady=5)
        self.entry.bind("<Return>", lambda e: self.send())
        self.send_btn = ctk.CTkButton(bar, text="Invia  ➤", width=110, height=44, corner_radius=22,
                                      fg_color=C["blue"], hover_color=C["blue_hover"],
                                      font=(self.font, 14, "bold"), command=self.send)
        self.send_btn.pack(side="right", padx=6, pady=6)

    def _build_footer(self):
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(fill="x", padx=24, pady=(0, 14))
        ctk.CTkButton(f, text="🧹  Pulizia automatica", height=38, corner_radius=19,
                      fg_color=C["orange"], hover_color="#FBBF24", text_color="#1A1200",
                      font=(self.font, 13, "bold"), command=self.run_full_clean).pack(side="left")
        ctk.CTkButton(f, text="⚙  Ottimizza disco", height=38, corner_radius=19, fg_color=C["violet"],
                      hover_color="#A78BFA", font=(self.font, 13, "bold"),
                      command=self.run_defrag).pack(side="left", padx=8)
        ctk.CTkButton(f, text="Nuova chat", height=38, width=110, corner_radius=19, fg_color=C["panel2"],
                      hover_color=C["border"], font=(self.font, 13), command=self.clear_chat).pack(side="left")
        self.widget_switch = ctk.CTkSwitch(f, text="Mini monitor", font=(self.font, 12), text_color=C["muted"],
                                           progress_color=C["blue"], command=self.toggle_widget)
        self.widget_switch.pack(side="right")
        if self.cfg.get("show_widget", True):
            self.widget_switch.select()
        self.model_menu = ctk.CTkOptionMenu(f, values=[self.cfg["model"]], width=170, height=34, corner_radius=17,
                                            fg_color=C["panel2"], button_color=C["border"],
                                            button_hover_color=C["blue"], font=(self.font, 12),
                                            command=self.change_model)
        self.model_menu.set(self.cfg["model"])
        self.model_menu.pack(side="right", padx=10)
        ctk.CTkLabel(f, text="Modello AI", text_color=C["muted"], font=(self.font, 12)).pack(side="right")

    # ---------- ollama ----------
    def _check_ollama(self):
        models = core.list_models(self.cfg["ollama_host"])
        def apply():
            if not models:
                self.set_status("Ollama non raggiungibile", C["red"])
                self.add_bubble("Non trovo Ollama. Avvialo (https://ollama.com) e scarica un modello, es.:\n"
                                "ollama pull qwen2.5:7b", "bot")
                return
            self.model_menu.configure(values=models)
            if self.cfg["model"] not in models:
                self.change_model(models[0])
            self.set_status(f"Ollama online · {self.cfg['model']}", C["green"])
        self.after(0, apply)

    def change_model(self, m):
        self.cfg["model"] = m
        self.model_menu.set(m)
        core.save_config(self.cfg)
        if self.status_color == C["green"]:
            self.set_status(f"Ollama online · {m}", C["green"])

    # ---------- azioni ----------
    def ask_confirm(self, title, detail=""):
        d = ConfirmDialog(self, title, detail, self.font)
        self.wait_window(d)
        return d.result

    def confirm_from_thread(self, name, args, desc):
        ev, box = threading.Event(), {}
        detail = "\n".join(f"{k}: {v}" for k, v in args.items())
        def ask():
            box["ok"] = self.ask_confirm(f"WinTAL vuole: {desc}", detail)
            ev.set()
        self.after(0, ask)
        ev.wait()
        return box.get("ok", False)

    def set_busy(self, on, text="WinTAL sta pensando…"):
        self.busy = on
        self.send_btn.configure(state="disabled" if on else "normal")
        if on:
            if not self.typing:
                self.typing = self.add_bubble(text, "bot")
                self._dots = 0
                self._animate()
            else:
                self.typing.label.configure(text=text)
        elif self.typing:
            self.typing.destroy()
            self.bubbles.remove(self.typing)
            self.typing = None

    def _animate(self):
        if not self.typing:
            return
        self._dots = (self._dots + 1) % 4
        base = self.typing.label.cget("text").rstrip(". …")
        self.typing.label.configure(text=base + "." * self._dots)
        self.after(400, self._animate)

    def status_from_thread(self, text):
        self.after(0, lambda: self.typing and self.typing.label.configure(text=text))

    def send(self, text=None):
        msg = (text or self.entry.get()).strip()
        if not msg or self.busy:
            return
        self.entry.delete(0, "end")
        self.add_bubble(msg, "user")
        self.set_busy(True)
        threading.Thread(target=self._agent, args=(msg,), daemon=True).start()

    def _agent(self, msg):
        reply = core.run_agent(msg, list(self.history), self.cfg,
                               confirm=self.confirm_from_thread, status=self.status_from_thread)
        self.history += [{"role": "user", "content": msg}, {"role": "assistant", "content": reply}]
        self.after(0, lambda: (self.set_busy(False), self.add_bubble(reply, "bot")))

    def _direct(self, label, title, detail, func):
        if self.busy or not self.ask_confirm(title, detail):
            return
        self.add_bubble(label, "user")
        self.set_busy(True, "Lavoro in corso…")
        def job():
            r = func()
            self.after(0, lambda: (self.set_busy(False), self.add_bubble(r, "bot")))
        threading.Thread(target=job, daemon=True).start()

    def run_full_clean(self):
        self._direct("Avvia la pulizia automatica", "Avviare la pulizia automatica?",
                     "Cancella file temporanei, svuota il Cestino e la cache di Windows Update.",
                     core.pulizia_automatica)

    def run_defrag(self):
        d = core.SYSTEM_DRIVE.rstrip("\\")
        self._direct(f"Ottimizza il disco {d}", f"Ottimizzare il disco {d}?",
                     "Windows sceglie da solo: TRIM sugli SSD, deframmentazione sugli HDD. "
                     "Serve avviare come amministratore.", lambda: core.deframmenta_disco(d))

    def clear_chat(self):
        if self.busy:
            return
        for b in self.bubbles:
            b.destroy()
        self.bubbles, self.history = [], []
        self.add_bubble("Chat azzerata. Di cosa hai bisogno?", "bot")

    def toggle_widget(self):
        on = bool(self.widget_switch.get())
        self.cfg["show_widget"] = on
        core.save_config(self.cfg)
        if on and not self.monitor:
            self.monitor = MonitorWidget(self, self.sensors, self.font)
        elif not on and self.monitor:
            self.monitor.destroy()
            self.monitor = None

    def bring_front(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def on_close(self):
        if self.monitor:
            self.monitor.destroy()
        self.destroy()


if __name__ == "__main__":
    MainApp().mainloop()
