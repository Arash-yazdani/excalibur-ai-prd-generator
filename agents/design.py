"""Design Consultant — completes q17–q26 and produces design-artifact.md."""
from __future__ import annotations

from .base import AgentConfig

DESIGN = AgentConfig(
    name="design",
    role_label="Senior Design Consultant (AI/UX)",
    owned_question_ids=[f"q{i}" for i in range(17, 27)],  # q17-q26 inclusive
    next_agent="develop",
    artifact_name="design-artifact",
)
