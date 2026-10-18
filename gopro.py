"""Керування GoPro HERO5 по Wi-Fi: HTTP API gpControl і keep-alive для потоку перегляду."""

import json
import urllib.request

DEFAULT_IP = "10.5.5.9"
HTTP_PORT = 80
STREAM_PORT = 8554  # камера шле MPEG-TS по UDP на цей порт комп'ютера
KEEPALIVE_MSG = b"_GPHD_:0:0:2:0.000000\n"
KEEPALIVE_INTERVAL = 2.0  # без keep-alive камера зупиняє потік за ~5 с

_START_PATH = "/gp/gpControl/execute?p1=gpStream&a1=proto_v2&c1=restart"
_INFO_PATH = "/gp/gpControl/info"

# Без проксі: системний проксі Windows не знає про локальну мережу камери.
_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _url(ip, path):
    return f"http://{ip}:{HTTP_PORT}{path}"


def start_stream(ip, timeout=3.0):
    """Попросити камеру (пере)запустити потік перегляду. True, якщо камера відповіла."""
    try:
        with _http.open(_url(ip, _START_PATH), timeout=timeout) as r:
            r.read()
        return True
    except (OSError, ValueError):
        return False


def camera_info(ip, timeout=1.5):
    """{"model": ..., "firmware": ...} або None, якщо камера недоступна."""
    try:
        with _http.open(_url(ip, _INFO_PATH), timeout=timeout) as r:
            data = json.load(r)
    except (OSError, ValueError):
        return None
    info = data.get("info", {}) if isinstance(data, dict) else {}
    return {"model": info.get("model_name") or "GoPro",
            "firmware": info.get("firmware_version", "")}
