@echo off
cd /d "%~dp0"
echo Installing Login Tester dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Install failed. Check Python is installed: python --version
  pause
  exit /b 1
)
echo.
echo Done. Run start.bat to launch the server.
pause
