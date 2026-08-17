@echo off
REM ============================================================
REM  Zero Entry TextBack AI - stack launcher
REM  Starts uvicorn (port 8080) + localtunnel watchdog at login.
REM  Idempotent: skips anything already running.
REM  Logs to logs\stack.log
REM ============================================================
setlocal
cd /d C:\Users\Sevin\missed-call-ai
if not exist logs mkdir logs
set LOG=logs\stack.log

echo [%date% %time%] ===== stack launcher start ===== >> %LOG%

REM ---- 0) Hermes LLM proxy (port 8645) ----
netstat -ano | findstr LISTENING | findstr ":8645 " >nul 2>&1
if not errorlevel 1 (
    echo [%date% %time%] LLM proxy already listening on 8645 - skip >> %LOG%
) else (
    echo [%date% %time%] Starting Hermes LLM proxy... >> %LOG%
    start "TextBack LLM proxy" /min cmd /c "hermes proxy start --provider nous --port 8645 >> logs\proxy.log 2>&1"
)

REM ---- 1) uvicorn server ----
netstat -ano | findstr LISTENING | findstr ":8080 " >nul 2>&1
if not errorlevel 1 (
    echo [%date% %time%] Server already listening on 8080 - skip >> %LOG%
) else (
    echo [%date% %time%] Starting uvicorn... >> %LOG%
    start "TextBack uvicorn" /min cmd /c "cd /d C:\Users\Sevin\missed-call-ai && python -m uvicorn app:app --host 0.0.0.0 --port 8080 >> logs\server.log 2>&1"
)

REM ---- 2) localtunnel watchdog ----
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object {$_.CommandLine -like '*localtunnel_watchdog*'}) { exit 0 } else { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo [%date% %time%] Watchdog already running - skip >> %LOG%
) else (
    echo [%date% %time%] Starting watchdog... >> %LOG%
    start "TextBack tunnel watchdog" /min cmd /c "cd /d C:\Users\Sevin\missed-call-ai && python localtunnel_watchdog.py >> logs\watchdog.log 2>&1"
)

REM ---- 3) wait for server + health check ----
ping -n 11 127.0.0.1 >nul 2>&1
curl -s -m 5 http://localhost:8080/health >> %LOG% 2>&1
echo. >> %LOG%

endlocal
