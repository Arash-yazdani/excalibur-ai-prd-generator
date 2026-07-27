# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.0] — Unreleased

Runs on any Claude credential, not just a subscription.

### Added
- **Provider-agnostic credentials.** Anthropic API key, LLM gateway, Amazon Bedrock, Google Cloud, Microsoft Foundry, Claude Platform on AWS, and Claude subscription are all detected and validated. None is privileged; subscription OAuth is now the fallback rather than the requirement.
- `tools/auth_preflight.detect_auth_mode()` and a Providers table in the README.
- Test suite (`tests/`) covering framework invariants, credential detection, and IO safety — including an assertion that every question `q1`–`q57` is owned by exactly one agent and that the ranges match `questions.json`'s phase boundaries.
- Ruff configuration and a CI workflow running lint + tests on Python 3.10 and 3.13.
- Retry with exponential backoff around each agent, so one transient provider error no longer kills a 50-minute pipeline.
- Post-condition check after each agent: unanswered questions are reported instead of silently shipping an incomplete PRD.
- `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`.

### Changed
- **`ANTHROPIC_API_KEY` is no longer stripped at startup.** Only Claude Desktop's four `CLAUDE_CODE_*` host-IPC variables are removed, which was the actual bug that stripping was working around.
- **No hardcoded default model.** `EXCALIBUR_MODEL` falls back to `ANTHROPIC_MODEL`, then to the provider's own default. A model id valid on the Anthropic API is wrong on Bedrock, Google Cloud, and Foundry.
- Prompts no longer bias generated PRDs toward one vendor: model selection at q27 now surveys across vendors, prompt-structure guidance at q21–q23 is convention-neutral, and the compliance sub-processor list no longer hardcodes a provider name.
- Cost reporting no longer claims every run is free — it is free on a subscription and a real charge on a metered credential.
- Product is named **EXCALIBUR** throughout. The package is now `excalibur-ai-prd-generator`, matching the repo; console scripts are `excalibur` and `excalibur-cli`.
- `uvicorn[standard]` → `uvicorn`; the five compiled extras were unused.
- `claude-agent-sdk` pinned to `>=0.2.0,<0.3.0` — the code uses recent `ClaudeAgentOptions` fields that a minor bump could rename.

### Fixed
- **Cross-origin API access.** Removed the `allow_origins=["*"]` CORS middleware. Combined with an unauthenticated API, it let any page the user visited read every PRD, delete projects, and start runs.
- **Stored XSS.** Agent-authored markdown reached the SPA's `innerHTML` unsanitized; a prompt injection could execute script in the app's origin. Now sanitized with `nh3`.
- **Event-loop freeze.** The credential probe ran synchronously inside an async handler, freezing every SSE stream and the whole UI for up to 90 seconds on each Run click.
- **Silently dropped agents.** Four agent imports were wrapped in `try/except ImportError: pass`; a typo in any of them removed that agent from the pipeline while the run still reported success.
- **Truncated project files.** `save_project` now writes atomically. A SIGTERM from the Cancel button mid-write could previously lose every answer in the run.
- **Path traversal in `read_artifact`.** It accepted model-supplied names without the validation its sibling `save_artifact` already had.
- Turn exhaustion (`max_turns`) is now surfaced instead of being logged and ignored.
- `prompts/develop.md` asked for a `web_search` tool that is actually named `WebSearch`.
- `prompts/discovery.md` claimed q1–q7 were human-authored; they come from the Intake agent.
- Generated PRDs were signed "Agentic PRD Processor", a name no longer used anywhere else.
- Removed dead imports, dead code, and the orphaned `assets/hero.svg`.

## [0.2.0] — 2026-06-26

Initial public release.
