@echo off
REM Crea dist\WinTAL_scan.exe
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --onefile --windowed --name WinTAL_scan --icon wintal.ico ^
  --collect-data customtkinter ^
  --add-data "logo_glossy.png;." --add-data "wintal.ico;." ^
  main.py
echo.
echo Fatto: trovi l'eseguibile in dist\WinTAL_scan.exe
pause
