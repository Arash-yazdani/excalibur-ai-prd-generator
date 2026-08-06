# Contributing

Thanks for taking a look. This is a small, deliberately dependency-light codebase, around 1,500 lines of Python plus a single-file frontend. Changes that keep it that way are the easiest to merge.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Set a credential (see the Providers table in the README), then check it:

```bash
python tools/auth_preflight.py
```

## Before you open a PR

```bash
ruff check .
pytest
```

Both run in CI on Python 3.10 and 3.13. The test suite is deliberately narrow: it covers the deterministic surface only, framework invariants, credential detection, and IO safety. It needs no network and no credential, and it should stay that way.

### Smoke-testing an actual run

The tests never call a model, so they can't catch a break in the agent loop itself. Two cheap ways to check that:

**Free, for the credential path.** [`examples/litellm-ollama.yaml`](examples/litellm-ollama.yaml) points EXCALIBUR at Ollama through a LiteLLM proxy, which proves gateway routing works end to end. Set `EXCALIBUR_PROBE_TIMEOUT=600` on the first run, loading a 6.6 GB model takes ~5m30s, though once warm a one-turn probe is ~11s. Whether a local model can actually drive the 8-tool agent loop is untested; that's the risk, not speed.

**Cheap, and the one that actually validates the pipeline.** Run the smallest agent on the cheapest model:

```bash
EXCALIBUR_MODEL=claude-haiku-4-5 EXCALIBUR_EFFORT=none EXCALIBUR_THINKING=none \
  python orchestrator.py only intake <project-id>
```

`EFFORT` and `THINKING` must be off: Haiku 4.5 rejects both parameters. Intake is 7 questions and 3 tools, so this costs cents and finishes in a minute or two.

## Where things live

| You want to change | Edit |
|---|---|
| What an agent asks or how it writes | `prompts/<agent>.md`: no code change needed |
| Which questions an agent owns | `agents/<agent>.py` **and** `framework/questions.json` (the tests enforce that they agree) |
| The question framework itself | `framework/questions.json`: the single source of truth for the UI, the agents, and the tests |
| The tools agents can call | `agents/base.py` (the MCP wrappers) and `tools/` (the implementations) |
| The UI | `static/index.html`: vanilla JS, no build step |

## Things worth knowing

- **`framework/questions.json` is load-bearing.** Question ownership is asserted in `tests/test_framework_invariants.py`. If you insert or renumber a question, that test will tell you what else needs updating, it exists because that mapping used to live in three unconnected places.
- **Agent behavior belongs in markdown, not Python.** The five consultant agents are one `AgentConfig` literal each; all the variation is in `prompts/`. Adding a seventh agent should be a dataclass and a prompt file.
- **Don't add a provider default.** Model selection deliberately falls through to the user's environment, because a model id valid on the Anthropic API is wrong on Bedrock, Google Cloud, and Foundry.
- **The tool layer is provider-neutral on purpose.** Nothing in `tools/` imports `claude_agent_sdk`. Keep it that way, it's what would make a different agent loop possible.
- **Agent output is untrusted.** It derives from whatever a user pasted. Anything rendered in the browser goes through `render_markdown()` in `server.py`.

## Adding a dependency

Please don't, unless it replaces more code than it adds. The current list is six runtime packages and the frontend has none.
