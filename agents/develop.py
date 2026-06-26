"""Develop Consultant — completes q27–q43, validates each subsection, produces develop-artifact.md.

Adds Claude Code's built-in `WebSearch` tool so the agent can validate the
model marketplace at q27 (Model Selection). Per the user requirement that
Model Selection "validate the marketplace" with footnoted reasoning rather
than picking a model arbitrarily.
"""
from __future__ import annotations

from .base import AgentConfig

DEVELOP = AgentConfig(
    name="develop",
    role_label="Develop Consultant (Senior AI Engineer)",
    owned_question_ids=[f"q{i}" for i in range(27, 44)],  # q27-q43 inclusive
    next_agent="deploy",
    artifact_name="develop-artifact",
    extra_builtin_tools=["WebSearch"],
)
