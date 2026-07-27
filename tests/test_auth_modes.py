"""Credential detection — the thing that decides whether a user can run this at all.

The point of these tests is that no credential is privileged: every supported
provider is detected, and subscription OAuth is the fallback rather than the
requirement.
"""
from __future__ import annotations

import pytest

from tools.auth_preflight import AUTH_MODES, HOST_IPC_VARS, detect_auth_mode, strip_host_ipc_env

ALL_CREDENTIAL_VARS = [var for var, _, _ in AUTH_MODES]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test from a bare environment."""
    for var in ALL_CREDENTIAL_VARS + list(HOST_IPC_VARS):
        monkeypatch.delenv(var, raising=False)


def test_no_credential_falls_back_to_subscription():
    mode, _label = detect_auth_mode()
    assert mode == "oauth"


@pytest.mark.parametrize(("var", "expected_mode", "_label"), AUTH_MODES)
def test_each_provider_is_detected(monkeypatch, var, expected_mode, _label):
    monkeypatch.setenv(var, "1")
    mode, label = detect_auth_mode()
    assert mode == expected_mode
    assert label


def test_cloud_provider_switch_wins_over_a_stray_api_key(monkeypatch):
    """CLAUDE_CODE_USE_* decides routing regardless of which key is also present."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    assert detect_auth_mode()[0] == "bedrock"


def test_empty_string_is_not_a_credential(monkeypatch):
    """An exported-but-empty var must not shadow the subscription path."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert detect_auth_mode()[0] == "oauth"


def test_strip_removes_host_ipc_vars_but_never_credentials(monkeypatch):
    """The Desktop IPC fix must not take the user's credential with it — this is
    the regression that made the tool subscription-only."""
    for var in HOST_IPC_VARS:
        monkeypatch.setenv(var, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-keepme")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.com")

    strip_host_ipc_env()

    import os

    for var in HOST_IPC_VARS:
        assert var not in os.environ, f"{var} should have been stripped"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-keepme"
    assert os.environ["ANTHROPIC_BASE_URL"] == "https://gateway.example.com"


def test_anthropic_api_key_is_not_in_the_strip_list():
    assert "ANTHROPIC_API_KEY" not in HOST_IPC_VARS
