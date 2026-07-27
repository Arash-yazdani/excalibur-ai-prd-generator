"""Centralized path resolution. All tools, agents, and the server go through these."""
from __future__ import annotations

import os
from pathlib import Path

# Repo root (the EXCALIBUR folder). Resolved once at import time.
BASE_DIR = Path(__file__).resolve().parent.parent

# Canonical projects directory. Defaults to <BASE_DIR>/projects (consolidated
# from the legacy form tool location). Override via PROJECTS_DIR env var if you
# want to point EXCALIBUR at a Dropbox-synced or shared location.
PROJECTS_DIR = Path(
    os.environ.get("PROJECTS_DIR", str(BASE_DIR / "projects"))
).resolve()

MEMORY_DIR = BASE_DIR / "memory"
HANDOFFS_DIR = BASE_DIR / "handoffs"
ARTIFACTS_DIR = BASE_DIR / "artifacts"
PROMPTS_DIR = BASE_DIR / "prompts"
STATIC_DIR = BASE_DIR / "static"
RUNS_DIR = BASE_DIR / "runs"  # subprocess logs and pause flags
RUNS_LOG = BASE_DIR / "runs.jsonl"


def project_path(project_id: str) -> Path:
    """Return the JSON file path for a given project, in the canonical PROJECTS_DIR."""
    if not project_id or "/" in project_id or ".." in project_id:
        raise ValueError(f"Invalid project_id: {project_id!r}")
    return PROJECTS_DIR / f"{project_id}.json"


def project_handoffs_dir(project_id: str) -> Path:
    return HANDOFFS_DIR / project_id


def project_artifacts_dir(project_id: str) -> Path:
    return ARTIFACTS_DIR / project_id


def project_runs_dir(project_id: str) -> Path:
    """Per-project subprocess log directory. Each pipeline run gets a timestamped log here."""
    return RUNS_DIR / project_id


def pause_flag_path(project_id: str) -> Path:
    """Soft-pause flag. The orchestrator polls this between agents and exits cleanly if present."""
    return project_runs_dir(project_id) / "pause.flag"


def lock_path(project_id: str) -> Path:
    """One-run-at-a-time lock. Holds the subprocess PID while a pipeline is running."""
    return project_runs_dir(project_id) / "run.lock"


def ensure_project_dirs(project_id: str) -> None:
    project_handoffs_dir(project_id).mkdir(parents=True, exist_ok=True)
    project_artifacts_dir(project_id).mkdir(parents=True, exist_ok=True)
    project_runs_dir(project_id).mkdir(parents=True, exist_ok=True)
