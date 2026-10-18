"""Вікно-майстер: кроки з автоматичними перевірками, підказками і кнопкою «Старт».

Вигляд — Windows 11 (Fluent) через sv-ttk: картки кроків, анімовані значки стану,
підказки, що розгортаються, світла/темна тема за налаштуваннями Windows.
"""

import logging
import logging.handlers
import os
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import font as tkfont
from tkinter import ttk

import checks
import gopro
from bridge import NO_SIGNAL_TIMEOUT, Bridge

try:
    import sv_ttk
except ImportError:  # без теми програма працює зі стандартним виглядом
    sv_ttk = None

VERSION = "1.1.0"

RESOLUTIONS = {
    "1280×720 (рекомендовано)": (1280, 720),
    "1920×1080": (1920, 1080),
    "640×360": (640, 360),
}

OK, FAIL, WARN, WAIT, NEXT, IDLE = "ok", "fail", "warn", "wait", "next", "idle"
GLYPHS = {OK: "✓", FAIL: "✕", WARN: "!", NEXT: "→"}
FIREWALL_HINT_AFTER = 15  # секунд без відео -> підказка про брандмауер
CONTENT_WIDTH = 640  # фіксована ширина, щоб вікно не "стрибало" від кнопок і тексту
HELP_STATES = (FAIL, WARN, WAIT, NEXT)  # коли біля кроку показувати «Як це зробити?»

OBS_URL = "https://obsproject.com/download"
LOG_PATH = os.path.join(tempfile.gettempdir(), "GoProWebcam.log")

PALETTES = {
    "light": dict(bg="#fafafa", fg="#1b1b1b", muted="#616161", border="#e1e1e1", subtle="#f0f0f0",
                  accent="#005fb8", accent_hover="#003e92", ok="#0f7b0f", fail="#c42b1c",
                  warn="#9d5d00", next="#005fb8", idle="#c2c2c2", on_badge="#ffffff"),
    "dark": dict(bg="#1c1c1c", fg="#f3f3f3", muted="#a8a8a8", border="#353535", subtle="#272727",
                 accent="#4cc2ff", accent_hover="#99ebff", ok="#6ccb5f", fail="#ff99a4",
                 warn="#fce100", next="#4cc2ff", idle="#5c5c5c", on_badge="#1c1c1c"),
}

OBS_HELP = """OBS Studio потрібна лише як драйвер вебкамери — сама OBS під час роботи не запускається.

1. Натисни «Завантажити OBS» і встанови програму (усе за замовчуванням).
2. Відкрий OBS, натисни «Start Virtual Camera», потім «Stop Virtual Camera».
3. Закрий OBS і натисни «Перевірити знову»."""

GOPRO_HELP = """1. Увімкни камеру і постав її на зарядку (Wi-Fi швидко садить батарею).
2. Увімкни на камері Wi-Fi для застосунку:
   • HERO5 Black: свайп вниз → Connect → GoPro App
     (у деяких прошивках: Connect → Connect Device → GoPro App).
   • HERO5 Session: кнопкою Menu відкрий Wireless → GoPro App.
3. Камера покаже назву Wi-Fi мережі і пароль (HERO5 Black: Connect → Name & Password).
   Пароль невідомий? Скинь з'єднання (Reset Connections) — стандартний пароль зазвичай goprohero.
4. На комп'ютері натисни значок Wi-Fi біля годинника і підключись до мережі камери."""

INTERNET_HELP = """Wi-Fi адаптер підключається лише до однієї мережі. Поки він на GoPro, інтернету
через Wi-Fi немає, а Discord без нього не працює. Безкоштовні варіанти:
• Кабель Ethernet до роутера.
• Телефон по USB у режимі модема:
   – Android: Налаштування → Мережа → Точка доступу → USB-модем.
   – iPhone: Налаштування → Режим модема (потрібен драйвер Apple / iTunes)."""

FIREWALL_HELP = """Камера шле відео на порт 8554, і брандмауер Windows може його блокувати.
1. Натисни «Зупинити».
2. Пуск → «Брандмауер Захисника Windows» → «Дозволити взаємодію з програмою…».
3. «Змінити параметри» → знайди GoProWebcam (або «Дозволити іншу програму…» → exe).
4. Постав обидві галочки: «Приватна» і «Публічна» → ОК, і запусти камеру ще раз.
Також допомагає вимкнути й увімкнути Wi-Fi на камері."""

DISCORD_HELP = """1. Discord → ⚙ Налаштування користувача → Голос і відео.
2. У розділі «Відео» → Камера → вибери «OBS Virtual Camera».
3. Немає у списку? Повністю закрий Discord (і в треї біля годинника) та відкрий знову.
Кольорові смуги замість картинки = програма працює, але відео з GoPro ще не прийшло."""

GUIDE = """Програма бере картинку з GoPro по Wi-Fi і видає її як вебкамеру «OBS Virtual Camera». \
Її можна вибрати в Discord, Zoom, Teams, Telegram, Google Meet.

Йди кроками зверху вниз: програма сама перевіряє кожен крок і ставить ✓. \
Біля кожного кроку є «Як це зробити?» з покроковою підказкою.

Якість: це потік попереднього перегляду GoPro, приблизно 480p із затримкою ~0,5–1 с. \
Для дзвінків вистачає. Повну якість дає лише HDMI → карта захоплення.

Режим «Тільки запустити потік» (у Налаштуваннях) — для обробки відео в OBS: \
Sources → Media Source → зніми «Local File» → Input udp://@:8554, Input Format mpegts.

Журнал роботи (для діагностики): {log}""".format(log=LOG_PATH)


def system_prefers_dark():
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def style_title_bar(window, dark):
    """Темний заголовок вікна у темній темі (Windows 10 20H1+ / 11)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (нове і старе значення)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


class Badge(tk.Canvas):
    """Круглий значок стану кроку; у стані очікування — обертається."""

    SIZE = 30

    def __init__(self, parent, app, number):
        super().__init__(parent, width=self.SIZE, height=self.SIZE, highlightthickness=0, bd=0)
        self.app = app
        self.number = number
        self.state = None
        self._spin = None
        self._arc = None
        self._angle = 90

    def set(self, state):
        if state != self.state:
            self.state = state
            self.redraw()

    def stop(self):
        if self._spin:
            self.after_cancel(self._spin)
            self._spin = None

    def redraw(self):
        p = self.app.pal
        self.stop()
        self.configure(bg=p["bg"])
        self.delete("all")
        s, m = self.SIZE, 3
        if self.state in GLYPHS:
            color = p[self.state]
            self.create_oval(m, m, s - m, s - m, fill=color, outline=color)
            self.create_text(s / 2, s / 2, text=GLYPHS[self.state], fill=p["on_badge"],
                             font=self.app.f_badge)
        elif self.state == WAIT:
            self.create_oval(m, m, s - m, s - m, outline=p["border"], width=2.5)
            self._arc = self.create_arc(m, m, s - m, s - m, start=self._angle, extent=110,
                                        style="arc", outline=p["accent"], width=2.5)
            self._rotate()
        else:
            self.create_oval(m, m, s - m, s - m, outline=p["idle"], width=1.5)
            self.create_text(s / 2, s / 2, text=str(self.number), fill=p["muted"],
                             font=self.app.f_small_bold)

    def _rotate(self):
        self._angle = (self._angle - 24) % 360
        self.itemconfigure(self._arc, start=self._angle)
        self._spin = self.after(60, self._rotate)


class Step:
    """Картка кроку: значок, назва, підказка, кнопки дій і розгортувана інструкція."""

    def __init__(self, app, parent, number, title, help_text=None, actions=()):
        self.app = app
        self.card = tk.Frame(parent, highlightthickness=1, bd=0)
        app.paint(self.card, bg="bg", highlightbackground="border", highlightcolor="border")
        self.card.columnconfigure(1, weight=1)

        self.bar = tk.Frame(self.card, width=3)
        self.bar.grid(row=0, column=0, sticky="ns")
        body = tk.Frame(self.card)
        app.paint(body, bg="bg")
        body.grid(row=0, column=1, sticky="nsew", padx=(12, 14), pady=9)
        body.columnconfigure(1, weight=1)

        self.badge = Badge(body, app, number)
        self.badge.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 12), pady=(1, 0))
        title_label = tk.Label(body, text=title, font=app.f_h, anchor="w")
        app.paint(title_label, bg="bg", fg="fg")
        title_label.grid(row=0, column=1, sticky="w")
        self.hint = tk.Label(body, font=app.f_body, anchor="w", justify="left", wraplength=430)
        app.paint(self.hint, bg="bg", fg="muted")
        self.hint.grid(row=1, column=1, sticky="w", pady=(1, 0))

        # Праворуч: кнопки дій і посилання на інструкцію — без окремого рядка, щоб картка була низькою.
        side = tk.Frame(body)
        app.paint(side, bg="bg")
        side.grid(row=0, column=2, rowspan=2, sticky="e", padx=(12, 0))
        self.button_row = tk.Frame(side)
        app.paint(self.button_row, bg="bg")
        self.buttons = {}
        for text, command, style in actions:
            self.buttons[text] = ttk.Button(self.button_row, text=text, command=command, style=style)

        self.help = self.help_link = None
        if help_text:
            self.help_link = app.link(side, "Як це зробити?", self.toggle_help)
            self.help = tk.Label(body, text=help_text, font=app.f_small, anchor="w",
                                 justify="left", wraplength=CONTENT_WIDTH - 60, padx=12, pady=9)
            app.paint(self.help, bg="subtle", fg="fg")
            self.help.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
            self.help.grid_remove()

        self.current = False
        self._shown = None

    def set(self, state, hint, show_buttons=()):
        shown = (state, hint, tuple(show_buttons))
        if shown == self._shown:
            return
        self._shown = shown
        self.badge.set(state)
        self.hint.config(text=hint, wraplength=280 if show_buttons else 430)
        for b in self.buttons.values():
            b.pack_forget()
        for text in show_buttons:
            self.buttons[text].pack(side="left", padx=(6, 0))
        if show_buttons:
            self.button_row.grid(row=0, column=0, sticky="e")
        else:
            self.button_row.grid_remove()
        if self.help_link is not None:
            if state in HELP_STATES:
                self.help_link.grid(row=1, column=0, sticky="e", pady=(4, 0) if show_buttons else 0)
            else:
                self.help_link.grid_remove()
                if self.help.winfo_ismapped():
                    self.toggle_help()

    @property
    def state(self):
        return self.badge.state

    def set_current(self, current):
        if current != self.current:
            self.current = current
            self.repaint()

    def repaint(self):
        self.bar.configure(bg=self.app.pal["accent" if self.current else "bg"])
        self.badge.redraw()

    def toggle_help(self):
        if self.help.winfo_ismapped():
            self.help.grid_remove()
            self.help_link.config(text="Як це зробити?")
        else:
            self.help.grid()
            self.help_link.config(text="Сховати підказку")


class App:
    def __init__(self, root, theme=None, background_checks=True):
        self.root = root
        self.bridge = None
        self._closing = threading.Event()
        self._vcam_recheck = threading.Event()
        self._vcam_recheck.set()
        self._vcam_checking = False
        self._last_frames = 0
        self._last_tick = time.monotonic()
        self._fps = 0.0
        self._painted = []
        self._settings_win = None

        # Результати фонових перевірок (пишуться з потоку перевірок).
        self.vcam = None        # None | (bool, str)
        self.camera = None      # None | dict
        self.camera_checked = False
        self.internet = None    # None | bool
        self.ip_value = gopro.DEFAULT_IP

        self.mode = tk.StringVar(value="webcam")
        self.resolution = tk.StringVar(value=next(iter(RESOLUTIONS)))
        self.mirror = tk.BooleanVar(value=False)
        self.ip = tk.StringVar(value=gopro.DEFAULT_IP)

        root.title("GoPro Webcam")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._setup_fonts()
        self.themed = self._load_theme()
        self.dark = self.themed and (system_prefers_dark() if theme is None else theme == "dark")
        self._apply_ttk_theme()
        self._build()
        self.apply_theme(self.dark)

        if background_checks:
            threading.Thread(target=self._checker_loop, daemon=True).start()
        self.tick()

    # --- тема ----------------------------------------------------------------

    def _setup_fonts(self):
        families = set(tkfont.families(self.root))
        text = next((f for f in ("Segoe UI Variable Text", "Segoe UI") if f in families),
                    tkfont.nametofont("TkDefaultFont").actual("family"))
        display = next((f for f in ("Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI")
                        if f in families), text)
        self.f_title = tkfont.Font(self.root, family=display, size=17, weight="bold")
        self.f_h = tkfont.Font(self.root, family=text, size=11, weight="bold")
        self.f_body = tkfont.Font(self.root, family=text, size=10)
        self.f_small = tkfont.Font(self.root, family=text, size=9)
        self.f_small_bold = tkfont.Font(self.root, family=text, size=9, weight="bold")
        self.f_link = tkfont.Font(self.root, family=text, size=9)
        self.f_link_hover = tkfont.Font(self.root, family=text, size=9, underline=True)
        self.f_badge = tkfont.Font(self.root, family=text, size=11, weight="bold")

    def _load_theme(self):
        if sv_ttk is None:
            return False
        try:
            sv_ttk.set_theme("light", self.root)
            return True
        except tk.TclError:  # напр. стара Tk 8.5
            return False

    def _apply_ttk_theme(self):
        if self.themed:
            sv_ttk.set_theme("dark" if self.dark else "light", self.root)
        style = ttk.Style(self.root)
        style.configure("Big.Accent.TButton" if self.themed else "Big.TButton",
                        font=(self.f_h.actual("family"), 12, "bold"), padding=(12, 10))
        base = style.lookup(".", "background")
        self.pal = dict(PALETTES["dark" if self.dark else "light"])
        if base:
            self.pal["bg"] = base

    def apply_theme(self, dark):
        self.dark = dark
        self._apply_ttk_theme()
        self.root.configure(bg=self.pal["bg"])
        for widget, roles in self._painted:
            widget.configure(**{opt: self.pal[key] for opt, key in roles.items()})
        for step in self.steps:
            step.repaint()
        self.theme_button.config(text="◐")
        style_title_bar(self.root, dark)

    def toggle_theme(self):
        self.apply_theme(not self.dark)

    def paint(self, widget, **roles):
        """Запам'ятати кольори tk-віджета за ролями палітри (для перемикання теми)."""
        self._painted.append((widget, roles))
        if hasattr(self, "pal"):
            widget.configure(**{opt: self.pal[key] for opt, key in roles.items()})
        return widget

    def link(self, parent, text, command, bg="bg"):
        label = tk.Label(parent, text=text, font=self.f_link, cursor="hand2", bd=0)
        self.paint(label, bg=bg, fg="accent")
        label.bind("<Button-1>", lambda e: command())
        label.bind("<Enter>", lambda e: label.config(font=self.f_link_hover, fg=self.pal["accent_hover"]))
        label.bind("<Leave>", lambda e: label.config(font=self.f_link, fg=self.pal["accent"]))
        return label

    # --- розмітка ------------------------------------------------------------

    def _build(self):
        main = self.paint(tk.Frame(self.root), bg="bg")
        main.grid(row=0, column=0, sticky="nsew", padx=22, pady=(16, 14))
        main.columnconfigure(0, weight=1, minsize=CONTENT_WIDTH)

        head = self.paint(tk.Frame(main), bg="bg")
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.paint(tk.Label(head, text="GoPro Webcam", font=self.f_title, anchor="w"),
                   bg="bg", fg="fg").grid(row=0, column=0, sticky="w")
        self.paint(tk.Label(head, text="GoPro HERO5 як вебкамера для Discord, Zoom і Teams",
                            font=self.f_body, anchor="w"),
                   bg="bg", fg="muted").grid(row=1, column=0, sticky="w")
        tools = self.paint(tk.Frame(head), bg="bg")
        tools.grid(row=0, column=1, rowspan=2, sticky="ne")
        self.theme_button = ttk.Button(tools, text="◐", width=3, command=self.toggle_theme)
        if self.themed:
            self.theme_button.pack(side="left", padx=(0, 6))
        ttk.Button(tools, text="⚙", width=3, command=self.toggle_settings).pack(side="left", padx=(0, 6))
        ttk.Button(tools, text="Довідка", command=lambda: self.show_sheet("Як це працює", GUIDE)).pack(
            side="left")

        progress = self.paint(tk.Frame(main), bg="bg")
        progress.grid(row=1, column=0, sticky="ew", pady=(12, 10))
        progress.columnconfigure(0, weight=1)
        self.progress_label = self.paint(tk.Label(progress, font=self.f_small_bold, anchor="w"),
                                         bg="bg", fg="muted")
        self.progress_label.grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(progress, maximum=5, mode="determinate")
        self.progress.grid(row=1, column=0, sticky="ew", pady=(5, 0))

        steps = self.paint(tk.Frame(main), bg="bg")
        steps.grid(row=2, column=0, sticky="ew")
        steps.columnconfigure(0, weight=1)
        self.s_vcam = Step(self, steps, 1, "Драйвер вебкамери (OBS Studio)", OBS_HELP, [
            ("Завантажити OBS", lambda: webbrowser.open(OBS_URL), "Accent.TButton"),
            ("Перевірити знову", self.recheck_vcam, "TButton")])
        self.s_gopro = Step(self, steps, 2, "GoPro на зв'язку", GOPRO_HELP)
        self.s_net = Step(self, steps, 3, "Інтернет для Discord", INTERNET_HELP)
        self.s_stream = Step(self, steps, 4, "Відео з камери", FIREWALL_HELP)
        self.s_discord = Step(self, steps, 5, "Вибери камеру в Discord", DISCORD_HELP)
        self.steps = [self.s_vcam, self.s_gopro, self.s_net, self.s_stream, self.s_discord]
        for i, step in enumerate(self.steps):
            step.card.grid(row=i, column=0, sticky="ew", pady=(0, 7))

        self.button = ttk.Button(main, text="▶   Увімкнути камеру", command=self.toggle,
                                 style="Big.Accent.TButton" if self.themed else "Big.TButton")
        self.button.grid(row=3, column=0, sticky="ew", pady=(7, 10))

        foot = self.paint(tk.Frame(main), bg="bg")
        foot.grid(row=4, column=0, sticky="ew")
        foot.columnconfigure(1, weight=1)
        self.link(foot, "Журнал роботи", self.open_log).grid(row=0, column=0, sticky="w")
        self.paint(tk.Label(foot, text=f"v{VERSION}", font=self.f_small), bg="bg", fg="muted").grid(
            row=0, column=1, sticky="e")

    # --- діалоги -------------------------------------------------------------

    def _sheet(self, title):
        top = tk.Toplevel(self.root)
        top.withdraw()
        top.title(title)
        top.transient(self.root)
        top.resizable(False, False)
        top.configure(bg=self.pal["bg"])
        frame = tk.Frame(top, bg=self.pal["bg"])
        frame.pack(fill="both", expand=True, padx=26, pady=(20, 18))
        return top, frame

    def _present(self, top):
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + 60
        top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        top.deiconify()
        style_title_bar(top, self.dark)
        try:
            top.grab_set()
        except tk.TclError:  # вікно ще не показане (напр. у тестах)
            pass
        top.focus_set()

    def show_sheet(self, title, text, error=False):
        top, f = self._sheet(title)
        p = self.pal
        tk.Label(f, text=title, font=self.f_title if not error else self.f_h, bg=p["bg"],
                 fg=p["fail"] if error else p["fg"], anchor="w").pack(anchor="w")
        tk.Label(f, text=text, font=self.f_body, bg=p["bg"], fg=p["fg"], justify="left",
                 wraplength=470, anchor="w").pack(anchor="w", pady=(10, 18))
        ttk.Button(f, text="Зрозуміло", style="Accent.TButton" if self.themed else "TButton",
                   command=top.destroy).pack(anchor="e")
        top.bind("<Escape>", lambda e: top.destroy())
        self._present(top)
        return top

    def toggle_settings(self):
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.destroy()
            self._settings_win = None
            return
        top, f = self._sheet("Налаштування")
        p = self.pal
        f.columnconfigure(1, weight=1)

        def label(text, row, **grid):
            tk.Label(f, text=text, font=self.f_h, bg=p["bg"], fg=p["fg"], anchor="w").grid(
                row=row, column=0, columnspan=2, sticky="w", **grid)

        label("Режим", 0)
        widgets = [
            ttk.Radiobutton(f, text="Вебкамера для Discord (звичайний)", value="webcam",
                            variable=self.mode),
            ttk.Radiobutton(f, text="Тільки запустити потік (для OBS Media Source)",
                            value="keepalive", variable=self.mode)]
        widgets[0].grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        widgets[1].grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        label("Зображення", 3, pady=(16, 0))
        tk.Label(f, text="Роздільність", font=self.f_body, bg=p["bg"], fg=p["muted"]).grid(
            row=4, column=0, sticky="w", pady=(8, 0))
        combo = ttk.Combobox(f, textvariable=self.resolution, values=list(RESOLUTIONS),
                             state="readonly", width=28)
        combo.grid(row=4, column=1, sticky="e", pady=(8, 0))
        switch = ttk.Checkbutton(f, text="Дзеркально відобразити", variable=self.mirror,
                                 style="Switch.TCheckbutton" if self.themed else "TCheckbutton")
        switch.grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

        label("Камера", 6, pady=(16, 0))
        tk.Label(f, text="IP-адреса GoPro", font=self.f_body, bg=p["bg"], fg=p["muted"]).grid(
            row=7, column=0, sticky="w", pady=(8, 0))
        entry = ttk.Entry(f, textvariable=self.ip, width=18)
        entry.grid(row=7, column=1, sticky="e", pady=(8, 0))
        widgets += [combo, switch, entry]

        note = "Щоб змінити налаштування, спершу зупини камеру." if self.bridge else \
            "Зміни застосуються під час наступного запуску камери."
        tk.Label(f, text=note, font=self.f_small, bg=p["bg"], fg=p["muted"], anchor="w").grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(16, 12))
        ttk.Button(f, text="Готово", style="Accent.TButton" if self.themed else "TButton",
                   command=self.toggle_settings).grid(row=9, column=1, sticky="e")
        if self.bridge:
            for w in widgets:
                w.state(["disabled"])
        top.protocol("WM_DELETE_WINDOW", self.toggle_settings)
        top.bind("<Escape>", lambda e: self.toggle_settings())
        self._settings_win = top
        self._present(top)

    # --- дії ---------------------------------------------------------------

    def open_log(self):
        if not os.path.exists(LOG_PATH):
            self.show_sheet("Журнал роботи", f"Журнал ще порожній.\n\n{LOG_PATH}")
        elif sys.platform == "win32":
            os.startfile(LOG_PATH)
        else:
            webbrowser.open("file://" + LOG_PATH)

    def recheck_vcam(self):
        self.vcam = None
        self._vcam_recheck.set()

    def toggle(self):
        if self.bridge:
            self.stop_bridge()
            return
        if self._vcam_checking:
            self.show_sheet("Зачекай секунду", "Ще перевіряю віртуальну камеру, спробуй ще раз.")
            return
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self.toggle_settings()
        w, h = RESOLUTIONS[self.resolution.get()]
        self.bridge = Bridge(self.ip_value, w, h, self.mirror.get(),
                             keepalive_only=self.mode.get() == "keepalive")
        self.bridge.start()
        self.button.config(text="■   Зупинити")

    def stop_bridge(self):
        self.bridge.stop()
        self.bridge = None
        self.button.config(text="▶   Увімкнути камеру")

    def on_close(self):
        self._closing.set()
        for step in self.steps:
            step.badge.stop()
        if self.bridge:
            self.bridge.stop()
        self.root.destroy()

    # --- фонові перевірки ------------------------------------------------------

    def _checker_loop(self):
        while not self._closing.is_set():
            if self._vcam_recheck.is_set() and self.bridge is None:
                self._vcam_recheck.clear()
                self._vcam_checking = True
                self.vcam = checks.virtual_camera_status()
                self._vcam_checking = False
            if self.bridge is None:
                self.camera = gopro.camera_info(self.ip_value)
                self.camera_checked = True
            self.internet = checks.internet_available()
            self._closing.wait(3)

    # --- оновлення кроків --------------------------------------------------------

    def tick(self):
        self.ip_value = self.ip.get().strip() or gopro.DEFAULT_IP
        b = self.bridge
        if b:
            now = time.monotonic()
            self._fps = (b.frames_in - self._last_frames) / max(now - self._last_tick, 1e-6)
            self._last_frames, self._last_tick = b.frames_in, now
            if b.fatal_error:
                err = b.fatal_error
                self.stop_bridge()
                self.recheck_vcam()
                self.show_sheet("Не вдалося запустити", err, error=True)
        else:
            self._last_frames = 0

        keepalive = self.mode.get() == "keepalive"
        self._update_vcam(self.bridge, keepalive)
        self._update_gopro(self.bridge)
        self._update_internet()
        self._update_stream(self.bridge, keepalive)
        self._update_discord(self.bridge, keepalive)
        self._update_progress()
        self.root.after(500, self.tick)

    def _update_progress(self):
        done = sum(s.state == OK for s in self.steps)
        current = next((s for s in self.steps if s.state not in (OK, IDLE)), None)
        if current is None:
            current = next((s for s in self.steps if s.state == IDLE), None)
        for s in self.steps:
            s.set_current(s is current)
        if self.s_discord.state == NEXT:
            text = "Камера працює — залишилось вибрати її в Discord"
            done = 5
        else:
            text = f"Готово {done} з 5 кроків"
        self.progress_label.config(text=text)
        self.progress["value"] = done

    def _update_vcam(self, b, keepalive):
        if keepalive:
            self.s_vcam.set(IDLE, "У режимі «Тільки потік» віртуальну камеру вмикає сама OBS.")
        elif b and b.vcam_name:
            self.s_vcam.set(OK, f"Працює: «{b.vcam_name}»")
        elif self.vcam is None:
            self.s_vcam.set(WAIT, "Перевіряю…")
        elif self.vcam[0]:
            self.s_vcam.set(OK, f"Знайдено: «{self.vcam[1]}»")
        else:
            self.s_vcam.set(FAIL, "Не знайдено. Потрібна безкоштовна OBS Studio.",
                            ["Завантажити OBS", "Перевірити знову"])

    def _update_gopro(self, b):
        cam = self.camera
        name = cam["model"] if cam else "Камера"
        if b:
            if b.camera_reachable:
                self.s_gopro.set(OK, f"{name}: потік запущено")
            elif b.camera_reachable is None:
                self.s_gopro.set(WAIT, "Підключаюсь…")
            else:
                self.s_gopro.set(FAIL, "Зв'язок з камерою втрачено. Перевір, що комп'ютер "
                                       "підключений до Wi-Fi мережі GoPro.")
        elif cam:
            fw = f" · прошивка {cam['firmware']}" if cam["firmware"] else ""
            self.s_gopro.set(OK, f"Знайдено: {name}{fw}")
        elif not self.camera_checked:
            self.s_gopro.set(WAIT, "Шукаю камеру…")
        else:
            self.s_gopro.set(FAIL, "Не бачу камеру. Увімкни Wi-Fi на GoPro і підключи комп'ютер "
                                   "до її мережі.")

    def _update_internet(self):
        if self.internet is None:
            self.s_net.set(WAIT, "Перевіряю…")
        elif self.internet:
            self.s_net.set(OK, "Є, Discord доступний.")
        else:
            self.s_net.set(WARN, "Немає інтернету: Wi-Fi зайнятий камерою. Потрібен кабель або "
                                 "телефон по USB.")

    def _update_stream(self, b, keepalive):
        if not b:
            self.s_stream.set(IDLE, "Натисни «Увімкнути камеру» нижче.")
        elif keepalive:
            if b.camera_reachable:
                self.s_stream.set(OK, f"Потік іде. В OBS: Media Source → udp://@:{gopro.STREAM_PORT}, "
                                      "формат mpegts.")
            else:
                self.s_stream.set(WAIT, "Чекаю на камеру…")
        else:
            since = b.seconds_since_frame
            if since is not None and since <= NO_SIGNAL_TIMEOUT:
                sw, sh = b.source_size
                self.s_stream.set(OK, f"Іде: {sw}×{sh} · {self._fps:.0f} кадр/с")
            elif time.monotonic() - b.started_at > FIREWALL_HINT_AFTER:
                if b.packets_in == 0:
                    self.s_stream.set(WARN, "Від камери не приходить жодного пакета — найчастіше їх "
                                            "блокує брандмауер Windows.")
                else:
                    self.s_stream.set(WARN, f"Дані надходять ({b.packets_in} пакетів), але відео поки "
                                            "не розібрати. Перезапусти Wi-Fi на камері.")
            elif b.packets_in:
                self.s_stream.set(WAIT, "Отримую дані з камери, запускаю відео…")
            else:
                self.s_stream.set(WAIT, "Чекаю відео… Якщо Windows спитає дозвіл — дозволь.")

    def _update_discord(self, b, keepalive):
        hint = "Налаштування → Голос і відео → Камера → «OBS Virtual Camera»."
        if keepalive:
            hint = "В OBS натисни «Start Virtual Camera», потім у Discord: " + hint
        if not b:
            streaming = False
        elif keepalive:
            streaming = bool(b.camera_reachable)
        else:
            since = b.seconds_since_frame
            streaming = since is not None and since <= NO_SIGNAL_TIMEOUT
        self.s_discord.set(NEXT if streaming else IDLE, hint)


def setup_logging():
    handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=512 * 1024, backupCount=1,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    logging.getLogger("gopro_webcam").info("app start")


def main():
    try:
        setup_logging()
    except OSError:
        pass  # без журналу програма все одно працює
    root = tk.Tk()
    App(root)
    root.mainloop()
