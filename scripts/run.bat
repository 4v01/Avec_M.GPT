@echo off
setlocal
pushd "%~dp0\.."
call .venv\Scripts\activate || ( echo [ERROR] activate .venv first & popd & exit /b 1 )
python run_server.py
popd
