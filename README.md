<p align="center">
  <img src="assets/logo.svg" alt="EXCALIBUR" width="440">
</p>

<p align="center">
  <img src="assets/screenshot-landing.png" alt="AI PM EXCALIBUR — the project workspace" width="100%">
</p>

<h1 align="center">AI PM EXCALIBUR</h1>
<p align="center"><strong>Agentic PRD Generator</strong> — turn a paragraph of product context into a complete, stakeholder-ready Product Requirements Document, written by six specialist AI agents working in sequence.</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/UI-FastAPI%20%2B%20chat%20SPA-7c4dff.svg" alt="FastAPI">
  <img src="https://img.shields.io/badge/PRs-welcome-orange.svg" alt="PRs welcome">
</p>

---

## What it is

Paste in whatever you have — a deck export, an email thread, a one-pager, a transcript — and a pipeline of AI agents turns it into a structured **57-question PRD**. Each agent owns one phase, reads the previous agent's handoff, fills in its slice of the document, and passes a clean packet to the next. A final PM agent reviews the whole thing and produces a consolidated `final-prd.md`.

It runs locally as a single web app. One process, one port, a chat-style UI.

```
Intake → Discovery → Design → Develop → Deploy → PM Review
```

| Agent | Phase | Owns |
|-------|-------|------|
| **Intake** | Structure raw context | q1–q7 (Market & Business) |
| **Discovery** | Research & opportunity | q8–q16 |
| **Design** | UX & solution shape | q17–q26 |
| **Develop** | Architecture & build | q27–q43 |
| **Deploy** | Launch & GTM | q44–q57 |
| **PM Review** | Consolidate & sense-check | the whole PRD → `final-prd.md` |

Agents also keep a lightweight **cross-project memory**: after each run they append a few lessons to a markdown file, which is fed back into their context next time.

---

## Requirements

- **Python 3.10+**
- A **Claude Max subscription** and the **[Claude Code CLI](https://docs.claude.com/en/docs/claude-code)** installed.
  This tool drives the agent loop through Claude Code's OAuth — it authenticates against your Max subscription and **never uses an Anthropic API key** (it strips `ANTHROPIC_API_KEY` at startup on purpose). No per-token billing.

---

## Install

```bash
# 1. Clone
git clone https://github.com/YOUR-USERNAME/ai-pm-excalibur.git
cd ai-pm-excalibur

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# 3. Install the package and its dependencies
pip install -e .

# 4. Authenticate with your Claude Max subscription (one time)
#    This creates a persistent OAuth token — not an API key.
claude setup-token

# 5. Run
python server.py                     # → http://localhost:4500
```

Then open **http://localhost:4500** in your browser.

> **One-click launch:** on macOS double-click `start.command`; on Windows double-click `start.bat`. Both create the virtualenv, install dependencies, and start the server for you.

---

## Usage

### Web UI (recommended)
1. Open **http://localhost:4500**.
2. Create a project and paste your freeform product context.
3. Click **Run** — watch the agents work through the pipeline in real time.
4. Read the generated PRD, handoffs, and artifacts; export the final PRD.

### Command line
```bash
python orchestrator.py run <project-id>        # full pipeline
python orchestrator.py resume <project-id>     # resume after a pause
python orchestrator.py only design <project-id># run a single agent
python orchestrator.py pause <project-id>      # soft-pause between agents
```

---

## Configuration

All settings are optional — copy `.env.example` to `.env` to change defaults.

| Variable | Default | Purpose |
|----------|---------|---------|
| `PORT` | `4500` | Web server port |
| `PROJECTS_DIR` | `./projects` | Where project JSON files are stored (point it at a synced folder if you like) |
| `EXCALIBUR_MODEL` | `claude-opus-4-7` | Claude model the agents run on |

---

## Project structure

```
ai-pm-excalibur/
├── server.py            # FastAPI app: project CRUD, run control, SSE log stream
├── orchestrator.py      # CLI pipeline runner (run / resume / pause / reflect)
├── agents/              # The six agents (intake, discovery, design, develop, deploy, pm)
│   └── base.py          # Shared agent runtime + the in-process tool surface
├── framework/           # The 57-question framework (questions.json = source of truth)
├── prompts/             # One persona prompt per agent
├── tools/               # Project IO, handoffs, artifacts, run log, memory, auth preflight
├── memory/              # Cross-project lessons (starts empty)
├── static/              # The chat-style single-page UI
└── projects/            # Your project files live here (git-ignored)
```

---

## How it works

- **One source of truth.** `framework/questions.json` defines every phase, section, and question. The UI and the agents all read from it.
- **Scoped agents.** Each agent can only write the questions it owns. The PM agent is the only one allowed to edit across the whole document.
- **Handoff packets.** Between phases an agent writes a structured handoff (summary, decisions, constraints, open risks) that the next agent reads first.
- **Resumable.** Runs can be paused between agents and resumed; completed agents are skipped on resume.

---

## Contributing

Issues and PRs welcome. This is a focused, dependency-light codebase — keep changes small and the question framework backwards-compatible.

## License

[MIT](LICENSE).
