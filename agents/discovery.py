"""Discovery Consultant — completes q8–q16."""
from __future__ import annotations

from .base import AgentConfig

DISCOVERY = AgentConfig(
    name="discovery",
    role_label="Discovery Consultant (Senior MBA Strategy)",
    owned_question_ids=[f"q{i}" for i in range(8, 17)],  # q8-q16 inclusive
    next_agent="design",
    artifact_name=None,  # Discovery produces no stakeholder artifact; just handoff
)
