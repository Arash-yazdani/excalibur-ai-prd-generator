"""AI PM Manager, final review + consolidated final-prd.md.

Distinct from the other four agents in that:
- It owns no specific question range (`owned_question_ids = []`).
- It can write to any question (the base agent's `write_answer` tool grants
  write access for `cfg.name == "pm"`).
- It produces the project's terminal artifact.
- It has no `next_agent`: there is no handoff after PM.
"""
from __future__ import annotations

from .base import AgentConfig

PM = AgentConfig(
    name="pm",
    role_label="AI PM Manager (Senior AI Product Manager)",
    owned_question_ids=[],  # no fixed scope; PM can edit any q1-q57 to fix inconsistencies
    next_agent=None,  # terminal stage
    artifact_name="final-prd",
)
