@echo off
cd /d "%~dp0"
python -m pip show beautifulsoup4 >nul 2>&1
if errorlevel 1 (
  echo Missing dependencies. Running install.bat ...
  call "%~dp0install.bat"
)
echo Starting Login Tester at http://localhost:8080
python "%~dp0run.py"
if errorlevel 1 pause
