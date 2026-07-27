#!/bin/bash
# EXCALIBUR launcher (macOS) — double-click to start.
cd "$(dirname "$0")"
echo ""
echo "  ============================================="
echo "  EXCALIBUR — starting up…"
echo "  ============================================="
echo ""

# Strip Claude Desktop's host-IPC auth env vars before checking auth, so we
# test the same auth path the server will actually use. Without this, the
# CLI happily reports loggedIn:true (relying on env-injected IPC auth) but
# 401s mid-pipeline because the server's children fall outside that scope.
# Credentials (ANTHROPIC_API_KEY etc.) are deliberately left alone — see
# tools/auth_preflight.py for the supported providers.
unset CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH CLAUDE_CODE_ENTRYPOINT CLAUDECODE
if [ ! -d ".venv" ]; then
  echo "  First-time setup: creating venv + installing deps…"
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -e . >/dev/null
fi
if ! .venv/bin/python tools/auth_preflight.py; then
  echo ""
  read -n 1 -s -r -p "  Press any key to exit…"
  echo ""
  exit 1
fi
echo ""

sleep 1
open "http://localhost:4500"
.venv/bin/python server.py
