@echo off
REM Builds dist\CCCARE Tracker.exe from pc_app\app.py.
REM Run from this folder:  build_exe.bat   (re-run after changing any code)
cd /d "%~dp0"
py -m pip install -q -r pc_app\requirements.txt pyinstaller || goto :error
py -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "CCCARE Tracker" ^
  --paths pc_app ^
  --hidden-import pyttsx3.drivers ^
  --hidden-import pyttsx3.drivers.sapi5 ^
  --hidden-import comtypes.client ^
  --collect-submodules serial.tools ^
  --exclude-module pytest ^
  pc_app\app.py || goto :error
echo.
echo Done: dist\CCCARE Tracker.exe
echo The database (tracker.db) and raw_data\ are created next to the .exe.
exit /b 0
:error
echo Build failed.
exit /b 1
