"""Cross-project learning store.

Each agent has its own append-only markdown file under `memory/<agent>_lessons.md`.
The reflection step at the end of each run prompts the agent to append 1-3
concise lessons. Next run, those lessons are loaded and injected into the
agent's context (cached as part of the stable prefix).

Plain markdown — no embeddings, no vector store. Easy to inspect, hand-edit,
or roll back via git.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from .paths import MEMORY_DIR


def _lessons_path(agent: str) -> Path:
    safe = agent.lower().replace(" ", "_")
    if not safe.replace("_", "").isalnum():
        raise ValueError(f"Invalid agent name: {agent!r}")
    return MEMORY_DIR / f"{safe}_lessons.md"


def read_lessons(agent: str) -> str:
    """Return the full lessons markdown for an agent, or a placeholder if empty/new."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path = _lessons_path(agent)
    if not path.exists() or not path.read_text().strip():
        return "_(no lessons yet — this is your first project)_"
    return path.read_text()


def append_lesson(agent: str, lesson: str, project_id: str | None = None) -> str:
    """Append one dated lesson to the agent's lessons file. Returns confirmation."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path = _lessons_path(agent)
    if not path.exists():
        path.write_text(f"# Lessons — {agent}\n\n_Append-only. Each entry is one bullet, ≤ 50 words._\n\n")
    today = _dt.date.today().isoformat()
    suffix = f" _(from project: {project_id})_" if project_id else ""
    cleaned = lesson.strip().replace("\n", " ")
    if len(cleaned) > 800:
        cleaned = cleaned[:800] + "…"
    with path.open("a") as f:
        f.write(f"- **{today}**{suffix} — {cleaned}\n")
    return f"OK. Appended a {len(cleaned)}-char lesson to {path.name}."
