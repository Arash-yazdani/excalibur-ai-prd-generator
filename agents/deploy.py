"""Deploy Consultant, completes q44–q57."""
from __future__ import annotations

from .base import AgentConfig

DEPLOY = AgentConfig(
    name="deploy",
    role_label="Deploy Consultant (Senior Product Ops & GTM)",
    owned_question_ids=[f"q{i}" for i in range(44, 58)],  # q44-q57 inclusive
    next_agent="pm",
    artifact_name=None,  # PM produces the final consolidated artifact
)
