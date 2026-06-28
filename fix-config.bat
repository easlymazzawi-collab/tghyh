@echo off
cd /d "%~dp0"
echo Sua file config.json bi loi...
python "%~dp0run.py" --fix-config
echo.
echo Xong. Chay lai start.bat
pause
