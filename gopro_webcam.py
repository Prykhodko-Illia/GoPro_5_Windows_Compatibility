"""GoPro HERO5 -> віртуальна вебкамера для Windows (Discord, Zoom, Teams...).

Запуск: python gopro_webcam.py
Перевірка збірки: GoProWebcam.exe --self-test (код виходу 0 = усе на місці)
"""

import sys


def self_test():
    """Перевірити, що в exe потрапили всі залежності (запускається в CI)."""
    import av
    import numpy
    import pyvirtualcam  # noqa: F401
    import tkinter

    import bridge
    import checks  # noqa: F401
    import ui  # noqa: F401

    assert "h264" in av.codecs_available, "немає декодера h264"
    frame = av.VideoFrame.from_ndarray(numpy.zeros((480, 864, 3), numpy.uint8), format="rgb24")
    assert bridge.Bridge("127.0.0.1", 1280, 720)._fit(frame).shape == (720, 1280, 3)

    import sv_ttk
    root = tkinter.Tk()  # тема Windows 11 має бути вшита в exe
    root.withdraw()
    sv_ttk.set_theme("dark", root)
    root.destroy()
    return 0


def main():
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    import ui
    ui.main()


if __name__ == "__main__":
    main()
