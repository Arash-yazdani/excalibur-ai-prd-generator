"""Read and write the project's question state.

On-disk shape (backwards-compatible with the legacy form tool format):

    {
      "version": 1,
      "meta": {
        "name": "...", "owner": "...", "industry": "...", "date": "...",
        "intake_text": "..."         # NEW: freeform user input the Intake Agent reads
      },
      "data": {
        "q1": {"response": "...", "status": "complete" | "in-progress" | "needs-review" | "not-started"},
        ...
      }
    }

`meta.intake_text` is optional; old projects without it work fine.
"""
from __future__ import annotations

import json
import os
from typing import Any, Literal

from .paths import project_path

VALID_STATUSES = {"not-started", "in-progress", "complete", "needs-review"}
Status = Literal["not-started", "in-progress", "complete", "needs-review"]


def load_project(project_id: str) -> dict[str, Any]:
    path = project_path(project_id)
    if not path.exists():
        raise FileNotFoundError(f"Project not found: {path}")
    return json.loads(path.read_text())


def save_project(project_id: str, project: dict[str, Any]) -> None:
    path = project_path(project_id)
    # Atomic: this runs once per answered question, and the Cancel button SIGTERMs
    # the orchestrator. A bare write_text caught mid-flush truncates the file and
    # loses every answer in the run. Write beside the target, then rename over it.
    # Same indent as the form tool's server.py:118 so diffs stay readable.
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(project, indent=2))
    os.replace(tmp, path)


def get_response(project: dict[str, Any], qid: str) -> tuple[str, Status]:
    """Return (response_text, status) for a question. Defaults if missing."""
    cell = project.get("data", {}).get(qid) or {}
    return cell.get("response", ""), cell.get("status", "not-started")  # type: ignore[return-value]


def set_response(
    project: dict[str, Any],
    qid: str,
    response: str,
    status: Status = "complete",
) -> None:
    """Mutate the project dict in place. Caller persists with save_project()."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status!r}. Must be one of {sorted(VALID_STATUSES)}")
    project.setdefault("data", {})[qid] = {"response": response, "status": status}


def write_answer(project_id: str, qid: str, response: str, status: Status = "complete") -> str:
    """Atomic helper: load → set → save. Used as an agent tool.

    Returns a human-readable confirmation string the agent will see as
    the tool result.
    """
    project = load_project(project_id)
    set_response(project, qid, response, status)
    save_project(project_id, project)
    return f"OK. Wrote {len(response)} chars to {qid} (status: {status})."


# --- meta + intake_text helpers (for the Intake agent and the server's project CRUD) ---

VALID_META_FIELDS = {"name", "owner", "industry", "date", "intake_text"}


def get_meta(project: dict[str, Any]) -> dict[str, Any]:
    return project.setdefault("meta", {})


def get_intake_text(project_id: str) -> str:
    """Read the freeform intake text the human pasted into the project."""
    project = load_project(project_id)
    return project.get("meta", {}).get("intake_text", "") or ""


def set_intake_text(project_id: str, text: str) -> None:
    """Persist the freeform intake text that the Intake agent will read."""
    project = load_project(project_id)
    project.setdefault("meta", {})["intake_text"] = text or ""
    save_project(project_id, project)


def update_meta(project_id: str, **fields: str) -> str:
    """Patch any subset of meta fields. Used by the Intake agent's `set_meta` tool
    and the server's project-meta PATCH endpoint."""
    unknown = [k for k in fields if k not in VALID_META_FIELDS]
    if unknown:
        raise ValueError(
            f"Unknown meta fields: {unknown}. Valid: {sorted(VALID_META_FIELDS)}"
        )
    project = load_project(project_id)
    meta = project.setdefault("meta", {})
    changed: list[str] = []
    for k, v in fields.items():
        if v is None:
            continue
        meta[k] = str(v)
        changed.append(k)
    save_project(project_id, project)
    return f"OK. Updated meta fields: {changed}." if changed else "No changes."


def render_section_for_agent(project: dict[str, Any], section: dict[str, Any]) -> str:
    """Format a section's questions + current responses as markdown for an agent."""
    lines = [f"## {section['title']}", f"*{section['subtitle']}*", ""]
    for q in section["questions"]:
        response, status = get_response(project, q["id"])
        lines.append(f"### {q['id']}, {q['topic']} ({q['theme']})")
        lines.append(f"**Question:** {q['q']}")
        lines.append(f"**Tip:** {q['tip']}")
        lines.append(f"**Status:** {status}")
        if response.strip():
            lines.append(f"**Current response:**\n{response}")
        else:
            lines.append("**Current response:** _(empty)_")
        lines.append("")
    return "\n".join(lines)


def render_phase_for_agent(project: dict[str, Any], framework: dict[str, Any], phase_id: str) -> str:
    """Format an entire phase's content as markdown."""
    sections = [s for s in framework["sections"] if s["phase"] == phase_id]
    phase = next(p for p in framework["phases"] if p["id"] == phase_id)
    out = [f"# {phase['icon']}. {phase['label']}", ""]
    for s in sections:
        out.append(render_section_for_agent(project, s))
    return "\n".join(out)
