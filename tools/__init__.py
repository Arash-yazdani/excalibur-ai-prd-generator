"""Tool layer, pure functions exposed to agents via the SDK's tool runner.

All tools take a single `project_id` argument and read/write files relative
to the orchestrator's configured paths (PROJECTS_DIR, BASE_DIR). Tools are
designed to be agent-callable, failures return error strings rather than
raising, so the agent can read the error and adapt.
"""
