@echo off
setlocal ENABLEDELAYEDEXPANSION
title Crawler Suite - Build ONEFILE (franglish)

REM --- locate project root
set "THIS=%~dp0"
if exist "%THIS%src\" ( set "ROOT=%THIS%" ) else ( set "ROOT=%THIS%..\" )
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
echo [+] root: %ROOT%

REM --- ensure venv
set "VENV_ACT=%ROOT%\.venv\Scripts\activate.bat"
if not exist "%VENV_ACT%" (
  echo [!] venv not found. Running scripts\setup.bat ...
  if exist "%ROOT%\scripts\setup.bat" (
    call "%ROOT%\scripts\setup.bat" || ( echo [x] setup failed & exit /b 1 )
  ) else (
    echo [x] scripts\setup.bat missing; create venv manually.
    exit /b 1
  )
)
call "%VENV_ACT%" || ( echo [x] activate venv failed & exit /b 1 )

REM --- pyinstaller
where pyinstaller >nul 2>nul || python -m pip install -U pyinstaller

REM --- paths
set "SRC=%ROOT%\src"
set "FE=%ROOT%\frontend"
set "VAR=%ROOT%\var"
set "ENVF=%ROOT%\.env"
if not exist "%SRC%" ( echo [x] src not found & exit /b 1 )

REM --- clean
if exist "%ROOT%\dist"  rmdir /s /q "%ROOT%\dist"
if exist "%ROOT%\build" rmdir /s /q "%ROOT%\build"

REM --- add-data (onefile extracts to MEIPASS)
set "ADDFE="
set "ADDVAR="
set "ADDENV="
if exist "%FE%"  set "ADDFE=--add-data=""%FE%;frontend"""
if exist "%VAR%" set "ADDVAR=--add-data=""%VAR%;var"""
if exist "%ENVF%" set "ADDENV=--add-data=""%ENVF%;.env"""

echo [+] building onefile exe...
pyinstaller --noconfirm --clean --onefile ^
  --name crawler_server ^
  --paths "%SRC%" ^
  %ADDFE% %ADDVAR% %ADDENV% ^
  --hidden-import bs4 --hidden-import lxml --hidden-import pandas --hidden-import numpy --hidden-import sklearn ^
  --exclude-module torch --exclude-module torchvision --exclude-module torchaudio ^
  --exclude-module tensorflow --exclude-module onnxruntime ^
  "%ROOT%\run_server.py"

if errorlevel 1 ( echo [x] PyInstaller failed & exit /b 1 )

echo [OK] exe: %ROOT%\dist\crawler_server.exe
echo Tips: if Windows SmartScreen shows warning, click "More info" -> "Run anyway".
exit /b 0
