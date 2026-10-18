"""Знімки вікна-майстра в кількох станах (світла і темна тема) — для перевірки вигляду в CI.

Запуск (Windows): pip install pillow && python tests/screenshot_ui.py screenshots
"""

import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import ui  # noqa: E402

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)

from PIL import ImageGrab  # noqa: E402


class FakeBridge:
    keepalive_only = False
    fatal_error = None
    camera_reachable = True
    source_size = (864, 480)
    seconds_since_frame = 0.1
    vcam_name = "OBS Virtual Camera"

    def __init__(self):
        self.frames_in = 0
        self.packets_in = 0
        self.started_at = time.monotonic()

    def stop(self):
        pass


def window_rect(window):
    """Межі вікна разом із заголовком (без невидимих рамок Windows)."""
    window.update()
    if sys.platform == "win32":
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        rect = wintypes.RECT()
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect),
                                                      ctypes.sizeof(rect)) == 0:
            return rect.left, rect.top, rect.right, rect.bottom
    x, y = window.winfo_rootx(), window.winfo_rooty()
    return x, y, x + window.winfo_width(), y + window.winfo_height()


def settle(root, seconds=0.8):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.02)


def shoot(root, window, path):
    window.lift()
    settle(root)
    ImageGrab.grab(bbox=window_rect(window), all_screens=True).save(path)
    print("saved", path, flush=True)


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for theme in ("light", "dark"):
        root = tk.Tk()
        root.geometry("+30+10")
        root.attributes("-topmost", True)
        app = ui.App(root, theme=theme, background_checks=False)

        # 1. Перший запуск: OBS немає, камери не видно, інтернету немає; підказка розгорнута.
        app.vcam = (False, "Device not found")
        app.camera, app.camera_checked, app.internet = None, True, False
        app.s_gopro.toggle_help()  # на низькому екрані CI підказка відкриється окремим вікном
        sheets = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        if sheets:
            sheets[0].attributes("-topmost", True)
            shoot(root, sheets[0], os.path.join(out_dir, f"{theme}-1b-help-sheet.png"))
            for w in sheets:
                w.destroy()
        shoot(root, root, os.path.join(out_dir, f"{theme}-1-setup.png"))
        if app.s_gopro.help.winfo_ismapped():
            app.s_gopro.toggle_help()

        # 2. Пошук: значки очікування.
        app.vcam, app.camera_checked, app.internet = None, False, None
        shoot(root, root, os.path.join(out_dir, f"{theme}-2-checking.png"))

        # 3. Усе працює.
        app.vcam = (True, "OBS Virtual Camera")
        app.camera = {"model": "HERO5 Black", "firmware": "HD5.02.02.70.00"}
        app.camera_checked, app.internet = True, True
        fake = FakeBridge()
        app.bridge = fake
        app.button.config(text="■   Зупинити")

        def feed():
            fake.frames_in += 15
            fake.packets_in += 60
            root.after(500, feed)
        feed()
        settle(root, 1.2)
        shoot(root, root, os.path.join(out_dir, f"{theme}-3-running.png"))

        # 4. Налаштування (поверх працюючої камери).
        app.toggle_settings()
        app._settings_win.attributes("-topmost", True)
        shoot(root, app._settings_win, os.path.join(out_dir, f"{theme}-4-settings.png"))
        app.toggle_settings()

        # 5. Довідка.
        sheet = app.show_sheet("Як це працює", ui.GUIDE)
        sheet.attributes("-topmost", True)
        shoot(root, sheet, os.path.join(out_dir, f"{theme}-5-guide.png"))
        sheet.destroy()

        app.on_close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "screenshots")
