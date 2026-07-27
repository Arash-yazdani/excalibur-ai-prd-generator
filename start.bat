@echo off
REM EXCALIBUR launcher (Windows) — double-click to start.
cd /d "%~dp0"
echo.
echo   =============================================
echo   EXCALIBUR — starting up...
echo   =============================================
echo.

REM Strip Claude Desktop's host-IPC auth vars so we test the same auth path
REM the server will use. See orchestrator.py for full rationale. Credentials
REM are deliberately left alone — see tools\auth_preflight.py for providers.
set "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST="
set "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH="
set "CLAUDE_CODE_ENTRYPOINT="
set "CLAUDECODE="

if not exist ".venv" (
  echo   First-time setup: creating venv + installing deps...
  python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip >nul
  .venv\Scripts\python -m pip install -e . >nul
)

REM Credential preflight — detects whichever provider is configured.
.venv\Scripts\python tools\auth_preflight.py
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)
echo.
timeout /t 1 /nobreak >nul
start http://localhost:4500
.venv\Scripts\python server.py
