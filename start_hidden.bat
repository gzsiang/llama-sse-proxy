@echo off
:: llama-sse-proxy background launcher
:: Setup: copy config.bat.example to config.bat and edit it

:: Load user config (config.bat is not committed to repo)
if exist "%~dp0config.bat" call "%~dp0config.bat"

:: Default values
if not defined PYTHON set "PYTHON=python"
if not defined SCRIPT set "SCRIPT=%~dp0llama_sse_proxy.py"
if not defined BACKEND set "BACKEND=http://localhost:8080"
if not defined PORT set "PORT=8081"
if not defined LOG set "LOG=%~dp0proxy.log"
if not defined ERR set "ERR=%~dp0proxy.err"
if not defined SESSIONS_JSON set "SESSIONS_JSON_ARG=" else set "SESSIONS_JSON_ARG=--sessions-json %SESSIONS_JSON%"
if not defined OLLAMA_MODEL set "OLLAMA_MODEL_ARG=" else set "OLLAMA_MODEL_ARG=--ollama-model %OLLAMA_MODEL%"

:: Clear stale log files to avoid permission errors
del "%LOG%" 2>nul
del "%ERR%" 2>nul

:: Check if already running
for /f "tokens=2" %%a in ('wmic process where "name='python.exe' and commandline like '%%llama_sse_proxy%%'" get processid 2^>nul') do (
    echo Proxy already running (PID %%a). Stop it first.
    exit /b 1
)

echo Starting llama-sse-proxy...
echo   Backend: %BACKEND%
echo   Port:    %PORT%
echo.

powershell -WindowStyle Hidden -Command ^
    "Start-Process -FilePath '%PYTHON%' -ArgumentList ('\"%SCRIPT%\",\"--backend\",\"%BACKEND%\",\"--port\",\"%PORT%\",\"%SESSIONS_JSON_ARG%\",\"%OLLAMA_MODEL_ARG%\"') -WindowStyle Hidden -RedirectStandardOutput '%LOG%' -RedirectStandardError '%ERR%'"
