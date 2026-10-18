"""Потік GoPro -> декодер -> віртуальна вебкамера. Усе працює у фонових потоках."""

import socket
import threading
import time

import av
import numpy as np

import gopro

FPS = 30
NO_SIGNAL_TIMEOUT = 3.0  # секунд без кадрів -> показуємо заставку
RESTART_AFTER = 6.0      # секунд без кадрів -> повторно просимо камеру почати потік


def color_bars(width, height):
    """Притемнені кольорові смуги: програма працює, але сигналу з камери немає."""
    colors = np.array(
        [[192, 192, 192], [192, 192, 0], [0, 192, 192], [0, 192, 0],
         [192, 0, 192], [192, 0, 0], [0, 0, 192]], dtype=np.uint8) // 3
    idx = np.arange(width) * len(colors) // width
    return np.ascontiguousarray(np.broadcast_to(colors[idx], (height, width, 3)))


class Bridge:
    def __init__(self, ip, width, height, mirror=False, keepalive_only=False):
        self.ip = ip
        self.width = width
        self.height = height
        self.mirror = mirror
        self.keepalive_only = keepalive_only  # тільки тримати потік живим (для OBS)

        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest = None
        self._latest_time = 0.0
        self._threads = []

        # Стан для інтерфейсу (читається з GUI-потоку).
        self.started_at = None
        self.camera_reachable = None  # None = ще не знаємо
        self.frames_in = 0
        self.source_size = None
        self.vcam_name = None
        self.fatal_error = None

    # --- керування -------------------------------------------------------

    def start(self):
        self.started_at = time.monotonic()
        targets = [self._keepalive_loop]
        if not self.keepalive_only:
            targets += [self._reader_loop, self._output_loop]
        self._threads = [threading.Thread(target=t, daemon=True) for t in targets]
        for t in self._threads:
            t.start()

    def stop(self):
        self.stop_event.set()
        for t in self._threads:
            t.join(timeout=4)

    @property
    def seconds_since_frame(self):
        with self._lock:
            if self._latest is None:
                return None
            return time.monotonic() - self._latest_time

    # --- камера ----------------------------------------------------------

    def _keepalive_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        last_start = -1e9
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if self.camera_reachable:
                    since = self.seconds_since_frame
                    stalled = not self.keepalive_only and (since is None or since > RESTART_AFTER)
                    need_start = stalled and now - last_start > RESTART_AFTER + 2
                else:
                    need_start = now - last_start > 4
                if need_start:
                    self.camera_reachable = gopro.start_stream(self.ip)
                    last_start = now
                try:
                    sock.sendto(gopro.KEEPALIVE_MSG, (self.ip, gopro.STREAM_PORT))
                except OSError:
                    self.camera_reachable = False
                self.stop_event.wait(gopro.KEEPALIVE_INTERVAL)
        finally:
            sock.close()

    # --- відео -----------------------------------------------------------

    def _fit(self, frame):
        """Вписати кадр у вихідну роздільність, зберігаючи пропорції (чорні поля)."""
        w, h = self.width, self.height
        scale = min(w / frame.width, h / frame.height)
        nw = max(2, int(frame.width * scale) // 2 * 2)
        nh = max(2, int(frame.height * scale) // 2 * 2)
        rgb = frame.to_ndarray(format="rgb24", width=nw, height=nh)
        if self.mirror:
            rgb = rgb[:, ::-1]
        canvas = np.zeros((h, w, 3), dtype=np.uint8)
        x0, y0 = (w - nw) // 2, (h - nh) // 2
        canvas[y0:y0 + nh, x0:x0 + nw] = rgb
        return canvas

    def _reader_loop(self):
        url = f"udp://@:{gopro.STREAM_PORT}?timeout=3000000&buffer_size=1048576"
        # Без fflags=nobuffer: він викидає пакети з етапу аналізу разом з першим ключовим кадром.
        options = {"flags": "low_delay", "probesize": "131072", "analyzeduration": "1000000"}
        while not self.stop_event.is_set():
            try:
                container = av.open(url, format="mpegts", options=options, timeout=(5, 3))
            except Exception:
                self.stop_event.wait(1)
                continue
            try:
                stream = container.streams.video[0]
                for packet in container.demux(stream):
                    if self.stop_event.is_set():
                        break
                    try:
                        frames = packet.decode()
                    except av.error.FFmpegError:
                        continue  # битий пакет по Wi-Fi - просто пропускаємо
                    for frame in frames:
                        img = self._fit(frame)
                        with self._lock:
                            self._latest = img
                            self._latest_time = time.monotonic()
                        self.source_size = (frame.width, frame.height)
                        self.frames_in += 1
            except Exception:
                pass  # таймаут або обрив - перепідключаємось
            finally:
                container.close()

    def _output_loop(self):
        try:
            import pyvirtualcam
            cam = pyvirtualcam.Camera(width=self.width, height=self.height, fps=FPS,
                                      fmt=pyvirtualcam.PixelFormat.RGB)
        except Exception as e:
            self.fatal_error = (
                "Не вдалося запустити віртуальну камеру.\n\n"
                "• Якщо OBS Studio не встановлена — встанови її (obsproject.com), відкрий один раз, "
                "натисни «Start Virtual Camera», потім «Stop Virtual Camera» і закрий OBS.\n"
                "• Якщо OBS відкрита з увімкненою віртуальною камерою — зупини її.\n\n"
                f"Деталі: {e}")
            self.stop_event.set()
            return
        placeholder = color_bars(self.width, self.height)
        with cam:
            self.vcam_name = cam.device
            while not self.stop_event.is_set():
                with self._lock:
                    img, t = self._latest, self._latest_time
                if img is None or time.monotonic() - t > NO_SIGNAL_TIMEOUT:
                    img = placeholder
                cam.send(img)
                cam.sleep_until_next_frame()
