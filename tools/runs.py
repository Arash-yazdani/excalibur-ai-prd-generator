"""Append-only run log. The dashboard reads this. One JSON line per state change."""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from .paths import RUNS_LOG


def log_event(project_id: str, agent: str, event: str, **extra: Any) -> None:
    """Append a single line to runs.jsonl.

    `event` is a short tag like 'agent_start', 'agent_complete', 'section_complete',
    'reflection_complete', 'pipeline_complete', 'error'.
    """
    record = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "project_id": project_id,
        "agent": agent,
        "event": event,
        **extra,
    }
    RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUNS_LOG.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def read_events(project_id: str | None = None) -> list[dict[str, Any]]:
    """Return all events (optionally filtered by project_id) in chronological order."""
    if not RUNS_LOG.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in RUNS_LOG.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if project_id and rec.get("project_id") != project_id:
            continue
        out.append(rec)
    return out


def all_project_ids() -> list[str]:
    """Return the set of project_ids that have any run history, newest run first."""
    seen: dict[str, str] = {}
    for rec in read_events():
        pid = rec.get("project_id")
        if pid and pid not in seen:
            seen[pid] = rec.get("ts", "")
    return sorted(seen.keys(), key=lambda k: seen[k], reverse=True)
