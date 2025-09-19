
@echo off
echo Checking scrapyd endpoints...
curl http://127.0.0.1:6800/daemonstatus.json
echo.
curl http://127.0.0.1:6800/listprojects.json
echo.
curl "http://127.0.0.1:6800/listspiders.json?project=pr_crawler"
echo.
