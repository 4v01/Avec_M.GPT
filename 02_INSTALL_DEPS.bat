@echo off
setlocal EnableExtensions
cd /d "%~dp0\..\.."
call .venv\Scripts\activate.bat || (echo venv not found & exit /b 1)
python -m pip install --upgrade pip
pip install -r requirements.txt || (echo deps failed & pause)