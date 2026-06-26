"""Claude Max subscription OAuth preflight for AI PM EXCALIBUR standalone runs.

AI PM EXCALIBUR never uses ANTHROPIC_API_KEY or pay-per-token API billing. It uses the
same OAuth path as Claude Code on a Max subscription (`claude auth login` /
`claude setup-token`).

`claude auth status` can report loggedIn:true while API calls 401 when:
  - Credentials live only in Claude Desktop's IPC env (stripped at startup)
  - macOS Keychain / on-disk OAuth tokens are expired
  - No on-disk ~/.claude/.credentials.json for SDK subprocesses

This module can materialize keychain creds to disk (macOS) and runs a minimal API
probe so we fail fast before a 50-minute pipeline.
"""
from __future__ import annotations

import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

STRIP_VARS = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDECODE",
)

_KEYCHAIN_SERVICE = "Claude Code-credentials"
_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


def strip_host_ipc_env() -> None:
    for var in STRIP_VARS:
        os.environ.pop(var, None)


def materialize_keychain_credentials() -> bool:
    """Copy macOS Keychain OAuth JSON to ~/.claude/.credentials.json if missing."""
    if _CREDENTIALS_PATH.exists():
        return True
    if platform.system() != "Darwin":
        return False
    try:
        user = os.environ.get("USER") or getpass.getuser()
    except Exception:
        user = "claude-code-user"
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                user,
                "-w",
                "-s",
                _KEYCHAIN_SERVICE,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0 or not result.stdout.strip():
        return False
    _CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CREDENTIALS_PATH.write_text(result.stdout.strip(), encoding="utf-8")
    try:
        os.chmod(_CREDENTIALS_PATH, 0o600)
    except OSError:
        pass
    return True


def _parse_auth_status(stdout: str) -> dict:
    try:
        return json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return {"loggedIn": "loggedIn: true" in stdout.lower()}


def check_auth_status(cli: str | None = None) -> tuple[bool, str, dict]:
    """Run `claude auth status` in the current (stripped) environment."""
    cli = cli or shutil.which("claude")
    if not cli:
        return (
            False,
            "bundled `claude` CLI not on PATH. Install Claude Code, then run "
            "`claude setup-token`.",
            {},
        )
    try:
        out = subprocess.run(
            [cli, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            env=os.environ.copy(),
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"could not run `claude auth status`: {e}", {}
    status = _parse_auth_status(out.stdout or "")
    if not status.get("loggedIn"):
        return (
            False,
            "Claude CLI is NOT logged in for Claude Max (OAuth). In Terminal.app run "
            "`claude auth login` or `claude setup-token`, sign in with your Max "
            "account in the browser, then restart AI PM EXCALIBUR.",
            status,
        )
    sub = status.get("subscriptionType", "?")
    method = status.get("authMethod", "?")
    return True, f"auth ok ({sub} via {method})", status


def probe_api(cli: str | None = None, *, timeout: int = 90) -> tuple[bool, str]:
    """Minimal real API call — catches status-ok-but-401 failures."""
    cli = cli or shutil.which("claude")
    if not cli:
        return False, "claude CLI not on PATH"
    try:
        proc = subprocess.run(
            [cli, "-p", "Reply with exactly: ok", "--max-turns", "1"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return False, "API auth probe timed out — try again or run `claude setup-token`."
    combined = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    if proc.returncode == 0 and "401" not in combined and "Failed to authenticate" not in combined:
        return True, "API probe ok"
    if "401" in combined or "Failed to authenticate" in combined or "Invalid authentication" in combined:
        return (
            False,
            "Claude Max OAuth returned 401 (subscription token expired or invalid). "
            "In Terminal.app (not Cursor): unset the CLAUDE_CODE_* host vars, then run "
            "`claude auth login` or `claude setup-token` and sign in with your Max account. "
            "Do not use an Anthropic API key — AI PM EXCALIBUR bills against Max only.",
        )
    snippet = combined.strip().splitlines()[-1] if combined.strip() else f"exit {proc.returncode}"
    return False, f"API auth probe failed: {snippet}"


def check_auth_ready(*, probe_api_call: bool = True) -> tuple[bool, str]:
    """Full preflight: optional disk materialization, status, optional API probe."""
    strip_host_ipc_env()
    materialize_keychain_credentials()
    cli = shutil.which("claude")
    ok, reason, _status = check_auth_status(cli)
    if not ok:
        return False, reason
    if not probe_api_call:
        return True, reason
    probe_ok, probe_reason = probe_api(cli)
    if not probe_ok:
        return False, probe_reason
    return True, f"{reason}; {probe_reason}"


def main() -> int:
    ok, reason = check_auth_ready(probe_api_call=True)
    print(reason)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
