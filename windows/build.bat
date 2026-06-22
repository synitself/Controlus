@echo off
REM Build a single-file Controlus.exe with PyInstaller.
setlocal
cd /d "%~dp0"

echo === Installing build dependencies ===
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

echo.
echo === Building Controlus.exe ===
python -m PyInstaller --noconfirm --clean Controlus.spec
if errorlevel 1 goto :error

echo.
echo === Done. Output: %~dp0dist\Controlus.exe ===
goto :eof

:error
echo.
echo Build failed.
exit /b 1
