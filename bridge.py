"""Потік GoPro -> декодер -> віртуальна вебкамера. Усе працює у фонових потоках."""

import logging
import socket
import threading
import time

import av
import numpy as np

import gopro

log = logging.getLogger("gopro_webcam")

FPS = 30
NO_SIGNAL_TIMEOUT = 3.0  # секунд без кадрів -> показуємо заставку
RESTART_AFTER = 6.0      # секунд без пакетів -> повторно просимо камеру почати потік
DECODE_STUCK_AFTER = 15.0  # пакети йдуть, а кадрів немає стільки секунд -> теж перезапуск
READ_TIMEOUT = 3.0       # секунд без UDP-пакетів -> перевідкриваємо декодер


def color_bars(width, height):
    """Притемнені кольорові смуги: програма працює, але сигналу з камери немає."""
    colors = np.array(
        [[192, 192, 192], [192, 192, 0], [0, 192, 192], [0, 192, 0],
         [192, 0, 192], [192, 0, 0], [0, 0, 192]], dtype=np.uint8) // 3
    idx = np.arange(width) * len(colors) // width
    return np.ascontiguousarray(np.broadcast_to(colors[idx], (height, width, 3)))


def open_stream_socket():
    """UDP-сокет на порту потоку. Через нього ж шлемо keep-alive, щоб камера відповідала сюди."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            # Windows: інакше ICMP "port unreachable" ламає recv помилкою WSAECONNRESET.
            sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        sock.bind(("", gopro.STREAM_PORT))
    except OSError:
        sock.close()
        raise
    sock.settimeout(0.5)
    return sock


class UdpStream:
    """Файлоподібний об'єкт для PyAV: віддає вміст UDP-датаграм (MPEG-TS) від камери."""

    def __init__(self, sock, stop_event, on_packet):
        self.sock = sock
        self.stop_event = stop_event
        self.on_packet = on_packet
        self._buf = b""
        self._eof = False

    def reset(self):
        """Перед новим av.open: знову чекати пакети до READ_TIMEOUT."""
        self._eof = False

    def read(self, size):
        if not self._buf:
            self._buf = self._next_datagram()
        chunk, self._buf = self._buf[:size], self._buf[size:]
        return chunk  # b"" = кінець потоку -> декодер перевідкриється

    def _next_datagram(self):
        # Після першого кінця потоку не чекаємо знову по READ_TIMEOUT на кожне читання,
        # інакше демуксер "помічає" обрив лише за ~12 с.
        deadline = time.monotonic() + (0 if self._eof else READ_TIMEOUT)
        while not self.stop_event.is_set() and time.monotonic() < deadline:
            try:
                data = self.sock.recv(65536)
            except (socket.timeout, ConnectionResetError):
                continue
            except OSError:
                return b""  # сокет закрито
            if data and not data.startswith(b"_GPHD_"):  # власний keep-alive (луна в тестах)
                self._eof = False
                self.on_packet()
                return data
        self._eof = True
        return b""


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
        self._last_packet_time = None
        self._threads = []
        self._sock = None

        # Стан для інтерфейсу (читається з GUI-потоку).
        self.started_at = None
        self.camera_reachable = None  # None = ще не знаємо
        self.packets_in = 0
        self.frames_in = 0
        self.source_size = None
        self.vcam_name = None
        self.fatal_error = None

    # --- керування -------------------------------------------------------

    def start(self):
        self.started_at = time.monotonic()
        targets = [self._keepalive_loop]
        if not self.keepalive_only:
            try:
                self._sock = open_stream_socket()
            except OSError as e:
                log.error("cannot bind UDP port %s: %s", gopro.STREAM_PORT, e)
                self.fatal_error = (
                    f"Порт {gopro.STREAM_PORT} уже зайнятий іншою програмою.\n\n"
                    "Закрий OBS (якщо в ній є Media Source з udp://@:8554) або другу копію "
                    f"GoPro Webcam і натисни «Старт» ще раз.\n\nДеталі: {e}")
                return
            targets += [self._reader_loop, self._output_loop]
        log.info("bridge start: ip=%s %dx%d mirror=%s keepalive_only=%s",
                 self.ip, self.width, self.height, self.mirror, self.keepalive_only)
        self._threads = [threading.Thread(target=t, daemon=True) for t in targets]
        for t in self._threads:
            t.start()

    def stop(self):
        self.stop_event.set()
        for t in self._threads:
            t.join(timeout=4)
        if self._sock:
            self._sock.close()
        log.info("bridge stop: packets=%d frames=%d", self.packets_in, self.frames_in)

    @property
    def seconds_since_frame(self):
        with self._lock:
            if self._latest is None:
                return None
            return time.monotonic() - self._latest_time

    def _stalled(self):
        """Потік зупинився: давно немає пакетів або пакети є, а кадри не декодуються."""
        now = time.monotonic()
        last_packet = self._last_packet_time
        if last_packet is None or now - last_packet > RESTART_AFTER:
            return True
        since_frame = self.seconds_since_frame
        if since_frame is None:
            return now - self.started_at > DECODE_STUCK_AFTER
        return since_frame > DECODE_STUCK_AFTER

    # --- камера ----------------------------------------------------------

    def _keepalive_loop(self):
        own = self._sock is None
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if own else self._sock
        last_start = -1e9
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if self.camera_reachable:
                    stalled = not self.keepalive_only and self._stalled()
                    need_start = stalled and now - last_start > RESTART_AFTER + 2
                else:
                    need_start = now - last_start > 4
                if need_start:
                    reachable = gopro.start_stream(self.ip)
                    if reachable != self.camera_reachable:
                        log.info("camera %s reachable: %s (packets so far: %d)",
                                 self.ip, reachable, self.packets_in)
                    self.camera_reachable = reachable
                    last_start = now
                try:
                    sock.sendto(gopro.KEEPALIVE_MSG, (self.ip, gopro.STREAM_PORT))
                except OSError as e:
                    log.info("keep-alive failed: %s", e)
                    self.camera_reachable = False
                self.stop_event.wait(gopro.KEEPALIVE_INTERVAL)
        finally:
            if own:
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

    def _count_packet(self):
        self.packets_in += 1
        self._last_packet_time = time.monotonic()

    def _reader_loop(self):
        udp = UdpStream(self._sock, self.stop_event, self._count_packet)
        options = {"probesize": "131072", "analyzeduration": "1000000"}
        while not self.stop_event.is_set():
            udp.reset()
            try:
                container = av.open(udp, format="mpegts", options=options)
            except Exception as e:
                if self.packets_in:
                    log.info("stream open failed: %s", e)
                continue  # без пакетів open сам чекає READ_TIMEOUT, окрема пауза не потрібна
            try:
                stream = container.streams.video[0]
                log.info("stream opened: %s %dx%d (packets: %d)", stream.codec_context.name,
                         stream.codec_context.width, stream.codec_context.height, self.packets_in)
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
                log.info("stream ended (packets: %d, frames: %d)", self.packets_in, self.frames_in)
            except Exception as e:
                log.info("stream interrupted: %r (packets: %d, frames: %d)",
                         e, self.packets_in, self.frames_in)
            finally:
                container.close()

    def _output_loop(self):
        try:
            import pyvirtualcam
            cam = pyvirtualcam.Camera(width=self.width, height=self.height, fps=FPS,
                                      fmt=pyvirtualcam.PixelFormat.RGB)
        except Exception as e:
            log.error("virtual camera unavailable: %s", e)
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
            log.info("virtual camera: %s", cam.device)
            while not self.stop_event.is_set():
                with self._lock:
                    img, t = self._latest, self._latest_time
                if img is None or time.monotonic() - t > NO_SIGNAL_TIMEOUT:
                    img = placeholder
                cam.send(img)
                cam.sleep_until_next_frame()
