@echo off
setlocal ENABLEDELAYEDEXPANSION
title Crawler Suite - Setup (franglish)

rem --- Make console UTF-8 (optional). All messages below are ASCII anyway.
chcp 65001 >nul 2>nul

rem --- Detect Python
where python >nul 2>nul || (
  echo [x] Python not found. Please install Python 3.10+ first.
  pause & exit /b 1
)
set PY=python

rem --- Create venv
if not exist "%~dp0..\..\.venv" (
  echo [+] Creating venv: .venv ...
  %PY% -m venv "%~dp0..\..\.venv" || ( echo [x] Failed to create venv. & pause & exit /b 1 )
)

rem --- Activate venv
call "%~dp0..\..\.venv\Scripts\activate.bat" || (
  echo [x] Failed to activate venv.
  pause & exit /b 1
)

rem --- pip base config
set PIP_DEFAULT_TIMEOUT=60
set PIP_DISABLE_PIP_VERSION_CHECK=1

rem --- Proxy (optional). Uncomment and edit if your company requires it.
rem set HTTPS_PROXY=http://user:pass@proxy.company.com:8080
rem set HTTP_PROXY=http://user:pass@proxy.company.com:8080

rem --- Corporate root CA (optional). Put PEM at scripts\corp.pem if needed.
if exist "%~dp0corp.pem" set PIP_CERT=%~dp0corp.pem
if exist "%~dp0corp.pem" set REQUESTS_CA_BUNDLE=%~dp0corp.pem

rem --- Indexes
set TRUST=--trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.tuna.tsinghua.edu.cn --trusted-host mirrors.aliyun.com
set IDX1=https://pypi.tuna.tsinghua.edu.cn/simple
set IDX2=https://mirrors.aliyun.com/pypi/simple/
set IDX3=https://pypi.org/simple

echo [+] Upgrading pip/setuptools/wheel (TUNA) ...
python -m pip install -U pip setuptools wheel -i %IDX1% %TRUST%
if errorlevel 1 (
  echo [!] TUNA failed, trying Aliyun mirror ...
  python -m pip install -U pip setuptools wheel -i %IDX2% %TRUST%
)
if errorlevel 1 (
  echo [!] Aliyun failed, trying official PyPI ...
  python -m pip install -U pip setuptools wheel -i %IDX3% %TRUST%
)
if errorlevel 1 (
  echo [x] Still cannot reach any index. Check proxy/cert or use offline mode (see HINTS below).
  goto :HINTS
)

rem --- Ensure requirements file exists; auto-generate a minimal one if missing
set REQ=%~dp0requirements-cpu.txt
if not exist "%REQ%" (
  echo [!] requirements-cpu.txt not found under scripts\. Generating a minimal one...
  >"%REQ%" echo flask==3.0.3
  >>"%REQ%" echo flask-cors==4.0.1
  >>"%REQ%" echo requests==2.32.3
  >>"%REQ%" echo beautifulsoup4==4.12.3
  >>"%REQ%" echo lxml==5.2.2
  >>"%REQ%" echo curl_cffi==0.6.2
  >>"%REQ%" echo numpy==1.26.4
  >>"%REQ%" echo scipy==1.13.1
  >>"%REQ%" echo scikit-learn==1.4.2
  >>"%REQ%" echo pandas==2.2.2
  >>"%REQ%" echo transformers==4.43.3
  >>"%REQ%" echo tokenizers==0.15.2
  >>"%REQ%" echo python-dotenv==1.0.1
)

rem --- Install base deps from requirements
echo [+] Installing base deps (TUNA) ...
python -m pip install -r "%REQ%" -i %IDX1% %TRUST%
if errorlevel 1 (
  echo [!] Switch to Aliyun mirror ...
  python -m pip install -r "%REQ%" -i %IDX2% %TRUST%
)
if errorlevel 1 (
  echo [!] Switch to official PyPI ...
  python -m pip install -r "%REQ%" -i %IDX3% %TRUST%
)
if errorlevel 1 (
  echo [x] Installing base deps failed.
  goto :HINTS
)

rem --- Install CPU torch separately (stable for Windows)
echo [+] Installing PyTorch CPU (official CPU wheel index) ...
python -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision torchaudio
if errorlevel 1 (
  echo [!] PyTorch CPU install failed. You can skip it now and install later.
)

echo.
echo [OK] Setup done. Ready to run:
echo     scripts\run.bat
echo or: python run_server.py
echo.
exit /b 0

:HINTS
echo.
echo ============ Troubleshooting (franglish) ============
echo 1) Proxy:
echo    - If your network needs proxy, set:
echo      set HTTPS_PROXY=http://user:pass@proxy.company.com:8080
echo      set HTTP_PROXY=http://user:pass@proxy.company.com:8080
echo.
echo 2) Corporate CA (MITM SSL inspection):
echo    - Put your root CA PEM at scripts\corp.pem, we auto-set PIP_CERT/REQUESTS_CA_BUNDLE.
echo.
echo 3) Offline mode:
echo    On a machine with internet:
echo      pip download -r scripts\requirements-cpu.txt -d d:\wheelhouse -i %IDX1%
echo    Copy d:\wheelhouse to this PC, then:
echo      python -m pip install --no-index --find-links d:\wheelhouse -r scripts\requirements-cpu.txt
echo.
echo 4) Debug commands:
echo      python -c "import ssl; print(ssl.OPENSSL_VERSION)"
echo      python -c "import certifi; print(certifi.where())"
echo      python -m pip config debug
echo =====================================================
echo.
pause
exit /b 1
