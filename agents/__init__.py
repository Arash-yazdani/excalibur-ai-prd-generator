"""The six agents that make up the EXCALIBUR pipeline.

Intake structures raw pasted context into q1–q7; five specialist consultants
(Discovery, Design, Develop, Deploy) fill their own question ranges in sequence,
and PM reviews across all of them to produce the final PRD.

Each agent is an `AgentConfig` (see base.py) that supplies:
- a static system prompt loaded from `prompts/<agent>.md`
- the slice of the framework it owns (which questions it may write)
- the handoff packet it produces for the next agent
- optionally, extra tools (Develop adds `WebSearch` for Model Selection)
"""
