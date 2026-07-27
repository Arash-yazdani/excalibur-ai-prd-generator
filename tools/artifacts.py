"""Stakeholder-shareable markdown artifacts produced by the Design, Develop, and PM agents."""
from __future__ import annotations

from .paths import ensure_project_dirs, project_artifacts_dir


def _check_name(name: str) -> None:
    """Artifact names come from the model, so validate on read as well as write —
    otherwise `read_artifact("../../../etc/hosts")` resolves outside the project."""
    if not name.replace("-", "").replace("_", "").isalnum():
        raise ValueError(
            f"Invalid artifact name: {name!r}. Use only alphanumeric, dashes, and underscores."
        )


def save_artifact(project_id: str, name: str, content: str) -> str:
    """Write `<artifacts>/<project-id>/<name>.md`. Returns the absolute path string."""
    _check_name(name)
    ensure_project_dirs(project_id)
    path = project_artifacts_dir(project_id) / f"{name}.md"
    path.write_text(content)
    return f"OK. Saved artifact to {path} ({len(content)} chars)."


def list_artifacts(project_id: str) -> list[str]:
    """List artifact filenames for a project (without `.md` suffix)."""
    d = project_artifacts_dir(project_id)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.md"))


def read_artifact(project_id: str, name: str) -> str:
    """Read an artifact by name. Used by later agents in the pipeline."""
    _check_name(name)
    path = project_artifacts_dir(project_id) / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Artifact not found: {path}")
    return path.read_text()
