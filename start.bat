@echo off
::: llama-sse-proxy launcher (visible window)
::: Setup: copy config.bat.example to config.bat and edit it

if exist "%~dp0config.bat" call "%~dp0config.bat"

if not defined BACKEND set "BACKEND=http://localhost:8080"
if not defined PORT   set "PORT=8081"
if not defined PYTHON  set "PYTHON=python"
if not defined SCRIPT  set "SCRIPT=%~dp0llama_sse_proxy.py"

set "SESSIONS_JSON_ARG="
if defined SESSIONS_JSON set "SESSIONS_JSON_ARG=--sessions-json %SESSIONS_JSON%"

set "OLLAMA_MODEL_ARG="
if defined OLLAMA_MODEL set "OLLAMA_MODEL_ARG=--ollama-model %OLLAMA_MODEL%"

echo Starting llama-sse-proxy...
echo   Proxy:   http://localhost:%PORT%
echo   Backend: %BACKEND%
if defined OLLAMA_MODEL echo   Ollama:  %OLLAMA_MODEL%
echo.
echo Press Ctrl+C to stop.
echo.

"%PYTHON%" "%SCRIPT%" --backend %BACKEND% --port %PORT% %SESSIONS_JSON_ARG% %OLLAMA_MODEL_ARG%
pause
