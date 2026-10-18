@echo off
chcp 65001 >nul
rem Збирає GoProWebcam.exe на Windows. Потрібен Python 3.11 (python.org, галочка "Add to PATH").
cd /d "%~dp0"
python -m venv .venv || goto :error
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller || goto :error
pyinstaller --noconfirm --onefile --windowed --name GoProWebcam ^
  --collect-all av --collect-all pyvirtualcam gopro_webcam.py || goto :error
echo.
echo Готово: dist\GoProWebcam.exe
pause
exit /b 0

:error
echo.
echo Помилка збирання. Подивись повідомлення вище.
pause
exit /b 1
