"""Credential preflight for EXCALIBUR standalone runs.

EXCALIBUR drives the agent loop through the Claude Code CLI, which accepts any of
the credential paths Claude Code itself supports. We detect which one the user has
configured and validate it before a long pipeline starts:

  - Console API key           ANTHROPIC_API_KEY
  - Gateway bearer token      ANTHROPIC_AUTH_TOKEN
  - LLM gateway               ANTHROPIC_BASE_URL
  - Amazon Bedrock            CLAUDE_CODE_USE_BEDROCK
  - Google Cloud              CLAUDE_CODE_USE_VERTEX
  - Microsoft Foundry         CLAUDE_CODE_USE_FOUNDRY
  - Claude Platform on AWS    CLAUDE_CODE_USE_ANTHROPIC_AWS
  - Claude subscription       `claude auth login` / `claude setup-token` (fallback)

Subscription OAuth needs extra care that the other modes don't: `claude auth status`
can report loggedIn:true while API calls 401 when credentials live only in Claude
Desktop's IPC env, when tokens are expired, or when there is no on-disk
~/.claude/.credentials.json for SDK subprocesses to read. So in that mode only, we
materialize keychain creds to disk (macOS) and check `auth status`.

Every mode ends with the same real API probe, because it is the only check that
actually proves the credential works.
"""
from __future__ import annotations

import getpass
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

# Claude Desktop injects these into shells it spawns to say "expect IPC-mediated auth
# from your parent host". Our CLI subprocess runs outside that IPC scope, so the
# handshake finds no listener and 401s mid-pipeline. Always strip them — this is a
# host-environment bug fix, unrelated to which credential the user chose.
HOST_IPC_VARS = (
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDECODE",
)

# (env var, mode id, human label). First match wins. Cloud-provider switches are
# checked before plain credentials because they decide routing regardless of which
# key is also present.
AUTH_MODES = (
    ("CLAUDE_CODE_USE_BEDROCK", "bedrock", "Amazon Bedrock"),
    ("CLAUDE_CODE_USE_VERTEX", "vertex", "Google Cloud"),
    ("CLAUDE_CODE_USE_FOUNDRY", "foundry", "Microsoft Foundry"),
    ("CLAUDE_CODE_USE_ANTHROPIC_AWS", "anthropic_aws", "Claude Platform on AWS"),
    ("ANTHROPIC_API_KEY", "api_key", "Anthropic API key"),
    ("ANTHROPIC_AUTH_TOKEN", "auth_token", "gateway bearer token"),
    ("ANTHROPIC_BASE_URL", "gateway", "LLM gateway"),
)

_KEYCHAIN_SERVICE = "Claude Code-credentials"
_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"

_SETUP_HINT = (
    "Set one of ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL, or a "
    "CLAUDE_CODE_USE_* provider switch — or run `claude setup-token` to use a Claude "
    "subscription. See the Providers table in the README."
)


def strip_host_ipc_env() -> None:
    """Remove Claude Desktop's host-IPC vars. Never touches the user's credentials."""
    for var in HOST_IPC_VARS:
        os.environ.pop(var, None)


def detect_auth_mode() -> tuple[str, str]:
    """Return (mode_id, label) for the credential the environment is configured for."""
    for var, mode, label in AUTH_MODES:
        if os.environ.get(var):
            return mode, label
    return "oauth", "Claude subscription (OAuth)"


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
    """Run `claude auth status`. Subscription-OAuth mode only — other modes have no
    login state to report and would fail this check while working fine."""
    cli = cli or shutil.which("claude")
    if not cli:
        return (
            False,
            "bundled `claude` CLI not on PATH. Install Claude Code, then configure a "
            "credential. " + _SETUP_HINT,
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
            "No credential found. The Claude CLI is not logged in and no API key, "
            "gateway, or cloud-provider variable is set.\n" + _SETUP_HINT,
            status,
        )
    sub = status.get("subscriptionType", "?")
    method = status.get("authMethod", "?")
    return True, f"auth ok ({sub} via {method})", status


def resolve_model() -> str | None:
    """The model the agents will actually use, or None to let the provider decide.

    Lives here rather than in agents/base.py so the preflight probes the same model
    the pipeline runs on. Probing a different one is worse than not probing: a
    gateway that only serves specific names passes or fails on the wrong question.
    """
    return os.environ.get("EXCALIBUR_MODEL") or os.environ.get("ANTHROPIC_MODEL") or None


def _first_real_error(text: str) -> str | None:
    """Pick the error out of CLI output, ignoring advisory lines.

    The CLI prints a `⚠ claude.ai connectors are disabled...` notice whenever a
    credential env var is set — which is always true in every non-subscription
    mode. Taking the last line blindly reports that warning as the failure and
    hides the real one.
    """
    for line in reversed([ln.strip() for ln in text.splitlines() if ln.strip()]):
        if line.startswith(("⚠", "warning:", "Warning:")):
            continue
        return line
    return None


def probe_timeout() -> int:
    """Seconds to allow the probe. 90 suits a hosted model; a local one served
    through a gateway can take minutes for the same single turn, so it's tunable."""
    try:
        return max(5, int(os.environ.get("EXCALIBUR_PROBE_TIMEOUT", "90")))
    except ValueError:
        return 90


def probe_api(cli: str | None = None, *, timeout: int | None = None, mode_label: str = "") -> tuple[bool, str]:
    """Minimal real API call — the only check that proves the credential works."""
    timeout = timeout if timeout is not None else probe_timeout()
    cli = cli or shutil.which("claude")
    if not cli:
        return False, "claude CLI not on PATH"
    cmd = [cli, "-p", "Reply with exactly: ok", "--max-turns", "1"]
    model = resolve_model()
    if model:
        cmd.extend(["--model", model])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return False, (
            f"API probe timed out after {timeout}s. If the credential is fine and the "
            f"model is just slow (a local model behind a gateway can take minutes for "
            f"one turn), raise EXCALIBUR_PROBE_TIMEOUT or set EXCALIBUR_SKIP_PROBE=1."
        )
    combined = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    if proc.returncode == 0 and "401" not in combined and "Failed to authenticate" not in combined:
        return True, f"API probe ok{f' ({model})' if model else ''}"
    if "401" in combined or "Failed to authenticate" in combined or "Invalid authentication" in combined:
        who = mode_label or detect_auth_mode()[1]
        return (
            False,
            f"Authentication failed (401) using {who}. Check that the credential is "
            f"valid and not expired. If you meant to use a Claude subscription, unset "
            f"any ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN and run `claude setup-token`.",
        )
    snippet = _first_real_error(combined) or f"exit {proc.returncode}"
    hint = ""
    if model:
        hint = f" (probing model {model!r} — confirm your provider serves that name)"
    return False, f"API probe failed{hint}: {snippet}"


def check_base_url_reachable(timeout: float = 3.0) -> tuple[bool, str]:
    """Fast TCP check against ANTHROPIC_BASE_URL, if one is set.

    Without this, pointing at a gateway that isn't running costs the full 90s
    probe timeout: the CLI retries with backoff rather than surfacing the
    connection refusal. A dead local proxy is the most common way to get here,
    and "nothing is listening" is a much more useful answer than "timed out".
    """
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if not base_url:
        return True, ""
    parsed = urlparse(base_url)
    host = parsed.hostname
    if not host:
        return False, f"ANTHROPIC_BASE_URL is not a valid URL: {base_url!r}"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except OSError as e:
        return False, (
            f"nothing is listening at {base_url} ({type(e).__name__}). "
            f"Start the gateway, or unset ANTHROPIC_BASE_URL to use a direct credential."
        )


def check_auth_ready(*, probe_api_call: bool = True) -> tuple[bool, str]:
    """Full preflight. Subscription mode gets keychain + status checks; every mode
    gets the API probe."""
    strip_host_ipc_env()
    mode, label = detect_auth_mode()
    cli = shutil.which("claude")

    reachable, why = check_base_url_reachable()
    if not reachable:
        return False, why

    if mode == "oauth":
        materialize_keychain_credentials()
        ok, reason, _status = check_auth_status(cli)
        if not ok:
            return False, reason
    else:
        if not cli:
            return False, "bundled `claude` CLI not on PATH. Install Claude Code."
        reason = f"using {label}"

    if not probe_api_call or os.environ.get("EXCALIBUR_SKIP_PROBE"):
        return True, f"{reason} (probe skipped)" if probe_api_call else reason
    probe_ok, probe_reason = probe_api(cli, mode_label=label)
    if not probe_ok:
        return False, probe_reason
    return True, f"{reason}; {probe_reason}"


def main() -> int:
    # Run standalone, this is the tool users are told to reach for when auth is
    # confusing — so it has to see the same environment the app does. server.py
    # and orchestrator.py load .env themselves before importing anything; without
    # the same call here, a correctly configured .env reports "no credential
    # found", which is the precise false negative this script exists to prevent.
    # Kept inside main() so importing the module stays side-effect free.
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
    except ImportError:
        pass

    mode, label = detect_auth_mode()
    print(f"credential: {label}")
    if model := resolve_model():
        print(f"model:      {model}")
    ok, reason = check_auth_ready(probe_api_call=True)
    print(f"result:     {reason}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
