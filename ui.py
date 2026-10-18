"""Вікно-майстер: кроки з автоматичними перевірками, підказками і кнопкою «Старт»."""

import logging
import logging.handlers
import os
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import checks
import gopro
from bridge import NO_SIGNAL_TIMEOUT, Bridge

RESOLUTIONS = {
    "1280×720 (рекомендовано)": (1280, 720),
    "1920×1080": (1920, 1080),
    "640×360": (640, 360),
}

OK, FAIL, WARN, WAIT, NEXT, IDLE = "ok", "fail", "warn", "wait", "next", "idle"
ICONS = {
    OK: ("✓", "#1a7f37"),
    FAIL: ("✗", "#cf222e"),
    WARN: ("!", "#bf8700"),
    WAIT: ("…", "#57606a"),
    NEXT: ("→", "#0969da"),
    IDLE: ("•", "#8c959f"),
}
FONT = "Segoe UI"
MUTED = "#57606a"
FIREWALL_HINT_AFTER = 15  # секунд без відео -> підказка про брандмауер

OBS_URL = "https://obsproject.com/download"
LOG_PATH = os.path.join(tempfile.gettempdir(), "GoProWebcam.log")

GOPRO_HELP = """Як підключити GoPro HERO5 до комп'ютера:

1. Увімкни камеру і постав її на зарядку (Wi-Fi швидко садить батарею).

2. Увімкни на камері Wi-Fi для застосунку:
   • HERO5 Black: свайп вниз → Connect → GoPro App
     (у деяких прошивках: Connect → Connect Device → GoPro App).
   • HERO5 Session: кнопкою Menu відкрий Wireless → GoPro App.

3. Камера покаже назву Wi-Fi мережі і пароль
   (на HERO5 Black також: Connect → Name & Password / Camera Info).
   Якщо пароль невідомий — скинь налаштування з'єднань (Reset Connections),
   після цього стандартний пароль зазвичай goprohero.

4. На комп'ютері натисни значок Wi-Fi біля годинника і підключись до мережі камери.

Коли все вийде, біля кроку «GoPro на зв'язку» з'явиться ✓."""

INTERNET_HELP = """Чому зникає інтернет?

Wi-Fi адаптер комп'ютера може бути підключений лише до однієї мережі.
Коли він підключений до GoPro, інтернету через Wi-Fi немає, а Discord без нього не працює.

Безкоштовні варіанти:
• Підключи інтернет кабелем (Ethernet) до роутера.
• Підключи телефон USB-кабелем і увімкни на ньому режим модема:
   – Android: Налаштування → Мережа → Точка доступу → USB-модем.
   – iPhone: Налаштування → Режим модема → увімкнути (потрібен iTunes / драйвер Apple)."""

FIREWALL_HELP = """Відео не приходить?

Камера шле відео на порт 8554 комп'ютера, і брандмауер Windows може його блокувати.

1. Натисни «Стоп».
2. Пуск → «Брандмауер Захисника Windows» → «Дозволити взаємодію з програмою…».
3. «Змінити параметри» → знайди GoProWebcam (або «Дозволити іншу програму…» і вибери exe).
4. Постав обидві галочки: «Приватна» і «Публічна» → ОК.
5. Натисни «Старт» ще раз.

Також допомагає перезапустити Wi-Fi на камері.

Не допомогло? Надішли тому, хто дав програму, файл журналу:
{log}""".format(log=LOG_PATH)

GUIDE = """Як це працює

Програма бере картинку з GoPro по Wi-Fi і видає її як вебкамеру «OBS Virtual Camera».
Її можна вибрати в Discord, Zoom, Teams, Telegram, Google Meet.

Просто йди кроками зверху вниз: програма сама перевіряє кожен крок і ставить ✓.
Біля кроку з ✗ є кнопка з поясненням, що робити.

Кольорові смуги в Discord означають, що програма працює, але відео з камери поки немає.

Якість: це потік попереднього перегляду GoPro, приблизно 480p із затримкою ~0,5–1 с.
Для дзвінків вистачає. Повну якість дає лише HDMI → карта захоплення.

Режим «Тільки запустити потік» (у Налаштуваннях) — для тих, хто хоче обробляти відео в OBS:
OBS → Sources → Media Source → зніми «Local File» → Input: udp://@:8554, Input Format: mpegts.

Журнал роботи (для діагностики): {log}""".format(log=LOG_PATH)


class Step:
    """Один рядок майстра: значок стану, назва, підказка, кнопки."""

    def __init__(self, parent, row, number, title, buttons=()):
        self.icon = tk.Label(parent, font=(FONT, 15, "bold"), width=2, anchor="n")
        self.icon.grid(row=row, column=0, rowspan=2, sticky="n", padx=(0, 6), pady=(6, 0))
        tk.Label(parent, text=f"{number}. {title}", font=(FONT, 10, "bold"), anchor="w").grid(
            row=row, column=1, sticky="w", pady=(8, 0))
        self.hint = tk.Label(parent, anchor="w", justify="left", wraplength=330, fg=MUTED,
                             font=(FONT, 9))
        self.hint.grid(row=row + 1, column=1, sticky="w")
        box = ttk.Frame(parent)
        box.grid(row=row, column=2, rowspan=2, sticky="e", padx=(8, 0), pady=(8, 0))
        self.buttons = {}
        for text, command in buttons:
            b = ttk.Button(box, text=text, command=command)
            b.pack(side="left", padx=(4, 0))
            self.buttons[text] = b
        self._shown = None

    def set(self, state, hint, show_buttons=()):
        shown = (state, hint, tuple(show_buttons))
        if shown == self._shown:
            return
        self._shown = shown
        ch, color = ICONS[state]
        self.icon.config(text=ch, fg=color)
        self.hint.config(text=hint)
        for text, b in self.buttons.items():
            if text in show_buttons:
                b.pack(side="left", padx=(4, 0))
            else:
                b.pack_forget()


class App:
    def __init__(self, root):
        self.root = root
        self.bridge = None
        self._closing = threading.Event()
        self._vcam_recheck = threading.Event()
        self._vcam_recheck.set()
        self._vcam_checking = False
        self._last_frames = 0
        self._last_tick = time.monotonic()
        self._fps = 0.0

        # Результати фонових перевірок (пишуться з потоку перевірок).
        self.vcam = None        # None | (bool, str)
        self.camera = None      # None | dict
        self.camera_checked = False
        self.internet = None    # None | bool
        self.ip_value = gopro.DEFAULT_IP

        root.title("GoPro Webcam")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        main = ttk.Frame(root, padding=(18, 14, 18, 14))
        main.grid(sticky="nsew")

        head = ttk.Frame(main)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        tk.Label(head, text="GoPro → вебкамера для Discord", font=(FONT, 15, "bold")).grid(
            row=0, column=0, sticky="w")
        ttk.Button(head, text="? Довідка", command=lambda: self.info("Довідка", GUIDE)).grid(
            row=0, column=1, sticky="e")
        tk.Label(head, text="Пройди кроки зверху вниз — галочки з'являться самі.",
                 fg=MUTED, font=(FONT, 9)).grid(row=1, column=0, sticky="w")

        steps = ttk.Frame(main)
        steps.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        steps.columnconfigure(1, weight=1)
        self.s_vcam = Step(steps, 0, 1, "Драйвер вебкамери (OBS Studio)", [
            ("Завантажити OBS", lambda: webbrowser.open(OBS_URL)),
            ("↻ Перевірити", self.recheck_vcam)])
        self.s_gopro = Step(steps, 2, 2, "GoPro на зв'язку", [
            ("Як підключити?", lambda: self.info("Підключення GoPro", GOPRO_HELP))])
        self.s_net = Step(steps, 4, 3, "Інтернет для Discord", [
            ("Що робити?", lambda: self.info("Інтернет", INTERNET_HELP))])
        self.s_stream = Step(steps, 6, 4, "Відео з камери", [
            ("Не працює?", lambda: self.info("Брандмауер", FIREWALL_HELP))])
        self.s_discord = Step(steps, 8, 5, "Вибери камеру в Discord")

        style = ttk.Style(root)
        style.configure("Big.TButton", font=(FONT, 12, "bold"), padding=(10, 8))
        self.button = ttk.Button(main, text="▶  Старт", style="Big.TButton", command=self.toggle)
        self.button.grid(row=2, column=0, sticky="ew", pady=(16, 8))

        self.settings_toggle = ttk.Button(main, text="⚙ Налаштування ▸", command=self.toggle_settings)
        self.settings_toggle.grid(row=3, column=0, sticky="w")
        self.settings = self._build_settings(main)
        self.settings.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.settings.grid_remove()

        threading.Thread(target=self._checker_loop, daemon=True).start()
        self.tick()

    def _build_settings(self, parent):
        f = ttk.LabelFrame(parent, text="Налаштування", padding=10)
        self.mode = tk.StringVar(value="webcam")
        ttk.Radiobutton(f, text="Вебкамера для Discord (звичайний режим)", value="webcam",
                        variable=self.mode).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(f, text="Тільки запустити потік (для OBS Media Source)", value="keepalive",
                        variable=self.mode).grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text="Роздільність:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.resolution = tk.StringVar(value=next(iter(RESOLUTIONS)))
        ttk.Combobox(f, textvariable=self.resolution, values=list(RESOLUTIONS), state="readonly",
                     width=24).grid(row=2, column=1, sticky="w", pady=(8, 0))
        self.mirror = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Дзеркально відобразити", variable=self.mirror).grid(
            row=3, column=1, sticky="w", pady=(6, 0))
        ttk.Label(f, text="IP камери:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.ip = tk.StringVar(value=gopro.DEFAULT_IP)
        ttk.Entry(f, textvariable=self.ip, width=16).grid(row=4, column=1, sticky="w", pady=(6, 0))
        self.setting_widgets = f.winfo_children()
        return f

    # --- дії ---------------------------------------------------------------

    def info(self, title, text):
        messagebox.showinfo(title, text, parent=self.root)

    def toggle_settings(self):
        if self.settings.winfo_ismapped():
            self.settings.grid_remove()
            self.settings_toggle.config(text="⚙ Налаштування ▸")
        else:
            self.settings.grid()
            self.settings_toggle.config(text="⚙ Налаштування ▾")

    def recheck_vcam(self):
        self.vcam = None
        self._vcam_recheck.set()

    def toggle(self):
        if self.bridge:
            self.stop_bridge()
            return
        if self._vcam_checking:
            self.info("Зачекай", "Ще перевіряю віртуальну камеру, спробуй за секунду.")
            return
        w, h = RESOLUTIONS[self.resolution.get()]
        self.bridge = Bridge(self.ip_value, w, h, self.mirror.get(),
                             keepalive_only=self.mode.get() == "keepalive")
        self.bridge.start()
        self.button.config(text="■  Стоп")
        for wdg in self.setting_widgets:
            wdg.state(["disabled"])

    def stop_bridge(self):
        self.bridge.stop()
        self.bridge = None
        self.button.config(text="▶  Старт")
        for wdg in self.setting_widgets:
            wdg.state(["!disabled"])

    def on_close(self):
        self._closing.set()
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
                messagebox.showerror("Помилка", err, parent=self.root)
        else:
            self._last_frames = 0

        keepalive = self.mode.get() == "keepalive"
        self._update_vcam(self.bridge, keepalive)
        self._update_gopro(self.bridge)
        self._update_internet()
        self._update_stream(self.bridge, keepalive)
        self._update_discord(self.bridge, keepalive)
        self.root.after(500, self.tick)

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
            self.s_vcam.set(FAIL, "Не знайдено. Встанови безкоштовну OBS Studio, відкрий її, натисни "
                                  "«Start Virtual Camera», потім «Stop» і закрий OBS. Далі — «↻ Перевірити».",
                            ["Завантажити OBS", "↻ Перевірити"])

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
                                       "підключений до Wi-Fi мережі GoPro.", ["Як підключити?"])
        elif cam:
            fw = f" (прошивка {cam['firmware']})" if cam["firmware"] else ""
            self.s_gopro.set(OK, f"Знайдено: {name}{fw}")
        elif not self.camera_checked:
            self.s_gopro.set(WAIT, "Шукаю камеру…")
        else:
            self.s_gopro.set(FAIL, "Не бачу камеру. Увімкни Wi-Fi на GoPro і підключи комп'ютер "
                                   "до її Wi-Fi мережі.", ["Як підключити?"])

    def _update_internet(self):
        if self.internet is None:
            self.s_net.set(WAIT, "Перевіряю…")
        elif self.internet:
            self.s_net.set(OK, "Є, Discord доступний.")
        else:
            self.s_net.set(WARN, "Немає інтернету. Wi-Fi зайнятий камерою — підключи кабель або "
                                 "телефон по USB у режимі модема.", ["Що робити?"])

    def _update_stream(self, b, keepalive):
        if not b:
            self.s_stream.set(IDLE, "Натисни «Старт» нижче.")
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
                self.s_stream.set(OK, f"Іде: {sw}×{sh}, {self._fps:.0f} кадр/с")
            elif time.monotonic() - b.started_at > FIREWALL_HINT_AFTER:
                if b.packets_in == 0:
                    self.s_stream.set(WARN, "Від камери не приходить жодного пакета. Найчастіше їх "
                                            "блокує брандмауер Windows — натисни «Не працює?».",
                                      ["Не працює?"])
                else:
                    self.s_stream.set(WARN, f"Дані з камери надходять ({b.packets_in} пакетів), але "
                                            "відео поки не розібрати. Вимкни й увімкни Wi-Fi на камері "
                                            "і натисни «Стоп» → «Старт».")
            elif b.packets_in:
                self.s_stream.set(WAIT, "Отримую дані з камери, запускаю відео…")
            else:
                self.s_stream.set(WAIT, "Чекаю відео… (якщо Windows спитає дозвіл — дозволь для "
                                        "приватних і публічних мереж)")

    def _update_discord(self, b, keepalive):
        hint = ("Discord → Налаштування → Голос і відео → Камера → «OBS Virtual Camera». "
                "Немає у списку — перезапусти Discord.")
        if keepalive:
            hint = "В OBS натисни «Start Virtual Camera». " + hint
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
