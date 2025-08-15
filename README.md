# Crawler Suite (Patched, EXE-ready)
1) scripts\setup.bat
2) scripts\run.bat  → http://127.0.0.1:5000/
If you see 'Frontend not bundled':
  PowerShell: $env:CRAWLER_FE_DIR="C:\path\to\crawler_suite_patch\frontend"
  CMD:        set CRAWLER_FE_DIR=C:\path\to\crawler_suite_patch\frontend
Build EXE: scripts\build_exe.bat
