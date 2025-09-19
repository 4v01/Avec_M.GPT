@echo off
setlocal EnableExtensions
cd /d "%~dp0\..\.."
if exist .venv\Scripts\python.exe (
    echo venv already exists
    goto :eof
)
python --version
python -m venv .venv || (echo venv create failed & pause)