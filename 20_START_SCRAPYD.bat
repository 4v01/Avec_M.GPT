
@echo off
setlocal enabledelayedexpansion

REM Resolve project root = two levels up from this script dir
set SCRIPT_DIR=%~dp0
for %%I in ("%SCRIPT_DIR%\..") do set PARENT=%%~fI
for %%I in ("%PARENT%\..") do set ROOT=%%~fI

if not exist "%ROOT%" (
  echo [ERR] Cannot resolve project root from %~dp0
  exit /b 1
)

set VENV=%ROOT%\.venv
if not exist "%VENV%" (
  echo [INFO] Creating root venv: "%VENV%"
  python -m venv "%VENV%"
)
call "%VENV%\Scripts\activate.bat"

python -m pip install -U pip wheel
python -m pip install -U scrapy scrapyd scrapyd-client

echo.
echo [OK] Using venv: %VENV%
echo [OK] Starting scrapyd at http://127.0.0.1:6800 ...
scrapyd
