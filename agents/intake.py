"""Intake Agent, runs first in the pipeline. Reads freeform user input and
populates q1-q7 + meta. Has a smaller, different tool surface than the five
consultant agents (it doesn't read other phases or write artifacts/handoffs)."""
from __future__ import annotations

from .base import AgentConfig

INTAKE = AgentConfig(
    name="intake",
    role_label="Intake Agent (Kickoff Briefing)",
    owned_question_ids=[f"q{i}" for i in range(1, 8)],  # q1-q7 inclusive
    next_agent="discovery",  # discovery picks up after, but intake itself does not write a handoff packet
    artifact_name=None,
)
