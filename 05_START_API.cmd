@echo off
setlocal ENABLEDELAYEDEXPANSION

REM uvicorn startup parameters:
REM --host 127.0.0.1       listen locally only
REM --port 8000            listening port
REM --reload               auto-reload on code changes (for development)
REM --log-level warning    log level set to warning
REM --no-access-log        disable access logs

REM Set project root directory more reliably
cd /d "%~dp0"
cd ..\..
set PROJECT_ROOT=%CD%

call ".venv\Scripts\activate"

REM create log directory if not exist
if not exist "%PROJECT_ROOT%\var\logs" mkdir "%PROJECT_ROOT%\var\logs"
if not exist "%PROJECT_ROOT%\api\var\logs" mkdir "%PROJECT_ROOT%\api\var\logs"
if not exist "%PROJECT_ROOT%\var\state" mkdir "%PROJECT_ROOT%\var\state"
if not exist "%PROJECT_ROOT%\var\out" mkdir "%PROJECT_ROOT%\var\out"

REM log control
set APP_LOG_REQUESTS=0
REM Set endpoints to skip logging
set APP_LOG_SKIP_ENDPOINTS=/api/progress,^
	/progress,^
	/assets/,^
	/favicon.ico
REM /api/progress  - progress API
REM /progress      - progress page
REM /assets/       - static resources
REM /favicon.ico   - favicon
set APP_LOG_ROTATE_MB=5
set APP_LOG_BACKUPS=7

REM Set PYTHONPATH to ensure correct import of api module
set PYTHONPATH=%PROJECT_ROOT%;%PYTHONPATH%
cd /d "%PROJECT_ROOT%"
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload --log-level warning --no-access-log

endlocal