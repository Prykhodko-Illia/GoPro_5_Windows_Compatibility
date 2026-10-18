"""Димовий тест вікна-майстра: проганяє всі стани кроків з фейковим потоком.

Запуск: python tests/test_ui.py   (код виходу 0 = вікно не падає в жодному стані)
"""

import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import ui  # noqa: E402


class FakeBridge:
    fatal_error = None
    source_size = (864, 480)
    frames_in = 0

    def __init__(self, keepalive_only, reachable, since, age=0.0, packets=0,
                 vcam_name="OBS Virtual Camera"):
        self.keepalive_only = keepalive_only
        self.packets_in = packets
        self.camera_reachable = reachable
        self.seconds_since_frame = since
        self.started_at = time.monotonic() - age
        self.vcam_name = vcam_name

    def stop(self):
        pass


def main():
    root = tk.Tk()
    root.withdraw()
    app = ui.App(root)
    app._closing.set()  # фонові перевірки не потрібні

    bridges = [None]
    for keepalive in (False, True):
        for reachable in (None, False, True):
            for since in (None, 0.1, 10.0):
                for age in (1.0, 30.0):
                    for packets in (0, 500):
                        bridges.append(FakeBridge(keepalive, reachable, since, age, packets))
    environments = [
        (None, None, False, None),
        ((False, "Device not found"), None, True, False),
        ((True, "OBS Virtual Camera"), {"model": "HERO5 Black", "firmware": "HD5.02"}, True, True),
        ((True, "OBS Virtual Camera"), {"model": "GoPro", "firmware": ""}, True, True),
    ]
    states = 0
    for vcam, camera, checked, internet in environments:
        app.vcam, app.camera, app.camera_checked, app.internet = vcam, camera, checked, internet
        for b in bridges:
            keepalive = bool(b and b.keepalive_only)
            app.mode.set("keepalive" if keepalive else "webcam")
            app._update_vcam(b, keepalive)
            app._update_gopro(b)
            app._update_internet()
            app._update_stream(b, keepalive)
            app._update_discord(b, keepalive)
            root.update_idletasks()
            states += 1

    # Повний цикл tick() зі "стрімом", що йде, і розгортання налаштувань.
    app.bridge = FakeBridge(False, True, 0.1)
    app.tick()
    app.toggle_settings()
    app.toggle_settings()
    app.bridge = None
    for step in (app.s_vcam, app.s_gopro, app.s_net, app.s_stream, app.s_discord):
        assert step.hint.cget("text"), "крок без підказки"
    app.on_close()
    print(f"ALL OK ({states} states)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
