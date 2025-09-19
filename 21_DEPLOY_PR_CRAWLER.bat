
@echo off
setlocal enabledelayedexpansion

REM Resolve project root
set SCRIPT_DIR=%~dp0
for %%I in ("%SCRIPT_DIR%\..") do set PARENT=%%~fI
for %%I in ("%PARENT%\..") do set ROOT=%%~fI

set VENV=%ROOT%\.venv
if not exist "%VENV%" (
  echo [ERR] Root venv not found at "%VENV%". Please run 20_START_SCRAPYD.bat first.
  exit /b 1
)
call "%VENV%\Scripts\activate.bat"

set SCRAPYD_URL=http://127.0.0.1:6800
set PROJECT=pr_crawler

REM Ensure scrapy & scrapyd-client are present in this venv
python -m pip install -U scrapy scrapyd-client

REM Move to crawler folder
set CRAWLER_DIR=%ROOT%\crawler
if not exist "%CRAWLER_DIR%" (
  echo [ERR] Crawler folder not found: "%CRAWLER_DIR%"
  exit /b 1
)
cd /d "%CRAWLER_DIR%"

REM Build & deploy. Use absolute path to scrapyd-deploy inside venv to avoid PATH issues.
set DEPLOY_EXE=%VENV%\Scripts\scrapyd-deploy.exe
if not exist "%DEPLOY_EXE%" (
  echo [ERR] scrapyd-deploy not found at "%DEPLOY_EXE%".
  echo       Try: python -m pip install scrapyd-client
  exit /b 1
)

echo [INFO] Deploying %PROJECT% to %SCRAPYD_URL%
"%DEPLOY_EXE%" default -p %PROJECT%
if errorlevel 1 (
  echo [ERR] scrapyd-deploy failed.
  exit /b 1
)

echo [OK] Deploy success.
