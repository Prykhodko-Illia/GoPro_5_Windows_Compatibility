"""Тест з імітацією GoPro: фейковий HTTP API + H.264 MPEG-TS по UDP на порт 8554.

Запуск: python tests/test_bridge.py   (код виходу 0 = усе пройшло)
"""

import http.server
import json
import logging
import os
import sys
import threading
import time

import av
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import bridge  # noqa: E402
import checks  # noqa: E402
import gopro  # noqa: E402

logging.basicConfig(level=logging.INFO, format="  log %(relativeCreated)6dms %(message)s",
                    stream=sys.stdout)
gopro.HTTP_PORT = 8080  # порт 80 часто потребує прав адміністратора
requests = []


class FakeGoPro(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        requests.append(self.path)
        body = b"{}"
        if self.path.endswith("/info"):
            body = json.dumps({"info": {"model_name": "HERO5 Black",
                                        "firmware_version": "HD5.02.02.70.00"}}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def send_stream(seconds, w=864, h=480):
    """Те, що шле HERO5: MPEG-TS з H.264 по UDP пакетами по 1316 байт."""
    out = av.open(f"udp://127.0.0.1:{gopro.STREAM_PORT}?pkt_size=1316", "w", format="mpegts")
    codec = "libx264" if "libx264" in av.codecs_available else "mpeg2video"
    s = out.add_stream(codec, rate=30)
    if codec == "libx264":
        s.options = {"tune": "zerolatency", "preset": "ultrafast", "g": "30"}
    s.width, s.height, s.pix_fmt = w, h, "yuv420p"
    for i in range(int(seconds * 30)):
        img = np.full((h, w, 3), 255, np.uint8)
        img[:, : (i * 8) % w] = (255, 0, 0)
        for p in s.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
            out.mux(p)
        time.sleep(1 / 30)
    for p in s.encode():
        out.mux(p)
    out.close()


results = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name, flush=True)
    results.append(cond)


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", gopro.HTTP_PORT), FakeGoPro)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    info = gopro.camera_info("127.0.0.1")
    check(f"camera_info -> {info}", info == {"model": "HERO5 Black", "firmware": "HD5.02.02.70.00"})

    # Віртуальну камеру не запускаємо (в CI її немає) - тільки keep-alive і декодер.
    b = bridge.Bridge("127.0.0.1", 1280, 720)
    b._output_loop = lambda: None
    b.start()
    check("bridge started", b.fatal_error is None)

    time.sleep(1)
    send_stream(4)
    check("camera reachable", b.camera_reachable is True)
    check("start stream requested", any("gpStream" in p for p in requests))
    check(f"UDP packets received ({b.packets_in})", b.packets_in > 100)
    check(f"frames decoded ({b.frames_in})", b.frames_in > 60)
    check(f"source size {b.source_size}", b.source_size == (864, 480))
    img = b._latest
    check(f"output shape {None if img is None else img.shape}",
          img is not None and img.shape == (720, 1280, 3))
    if img is not None:
        # 864x480 -> 1280x710: по 5 px чорних полів зверху і знизу
        check("letterbox bars are black", img[0].max() == 0 and img[4].max() == 0)
        check("picture inside", img[360, 1270].min() > 200)

    # Обрив потоку: заставка, повторний запит до камери, відновлення.
    n_before, starts_before = b.frames_in, sum("gpStream" in p for p in requests)
    time.sleep(13)
    since = b.seconds_since_frame
    check(f"no-signal detected ({since}s)", since is not None and since > bridge.NO_SIGNAL_TIMEOUT)
    check("stream restart re-requested", sum("gpStream" in p for p in requests) > starts_before)
    send_stream(3)
    check(f"recovered after drop (+{b.frames_in - n_before} frames)", b.frames_in - n_before > 40)

    # Другий екземпляр не може зайняти той самий порт -> зрозуміла помилка.
    dup = bridge.Bridge("127.0.0.1", 1280, 720)
    dup.start()
    check("busy port -> friendly error", dup.fatal_error is not None and "8554" in dup.fatal_error)
    b.stop()
    srv.shutdown()

    # Камера недоступна.
    b2 = bridge.Bridge("127.0.0.2", 1280, 720, keepalive_only=True)
    b2.start()
    time.sleep(4)
    check("unreachable camera reported", b2.camera_reachable is False)
    b2.stop()
    check("camera_info of missing camera is None", gopro.camera_info("127.0.0.2", timeout=1) is None)

    # Без OBS віртуальної камери немає - має бути зрозуміла помилка, а не падіння.
    ok, _ = checks.virtual_camera_status()
    if not ok:
        b3 = bridge.Bridge("127.0.0.2", 1280, 720)
        b3._output_loop()
        check("missing virtual camera -> friendly error",
              b3.fatal_error is not None and "OBS" in b3.fatal_error)

    check("color bars shape", bridge.color_bars(1280, 720).shape == (720, 1280, 3))

    print("ALL OK" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
