@echo off
setlocal EnableExtensions
cd /d "%~dp0\..\.."
call .venv\Scripts\activate.bat || (echo venv not found & exit /b 1)
REM === Ensure project root is on PYTHONPATH (pour imports absolus api.*) ===
set PYTHONPATH=%CD%
for %%P in (8000 8001 8002 8003 8004 8005 8006 8007 8008 8009) do (
  netstat -ano | findstr /R /C:":%%P .*LISTENING" >nul || ( set PORT=%%P & goto :FOUND )
)
echo [!] ports busy
exit /b 1
:FOUND
echo open: http://127.0.0.1:%PORT%
python -m uvicorn api.main:app --host 127.0.0.1 --port %PORT% --log-level info