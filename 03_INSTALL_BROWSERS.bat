@echo off
setlocal EnableExtensions
cd /d "%~dp0\..\.."
call .venv\Scripts\activate.bat || (echo venv not found & exit /b 1)
python -m playwright install chromium