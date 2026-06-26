"""Five specialist consultant agents that complete the Agentic PRD pipeline.

Each agent is a thin subclass of `ConsultantAgent` (in base.py) that supplies:
- a static system prompt loaded from `prompts/<agent>.md`
- the slice of the framework it owns (which questions to fill in)
- the handoff packet shape it produces
- optionally, extra tools (Develop adds `web_search` for Model Selection)
"""
