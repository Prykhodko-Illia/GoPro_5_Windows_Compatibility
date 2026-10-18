"""Перевірки середовища для майстра налаштування."""

import socket


def virtual_camera_status():
    """(True, назва пристрою) якщо віртуальна камера доступна, інакше (False, причина)."""
    try:
        import pyvirtualcam
        with pyvirtualcam.Camera(width=640, height=360, fps=5) as cam:
            return True, cam.device
    except Exception as e:
        return False, str(e)


def internet_available(host="discord.com", port=443, timeout=2.0):
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False
