"""Handoff packets — markdown deliverables that one agent writes for the next."""
from __future__ import annotations

from .paths import project_handoffs_dir, ensure_project_dirs

# Pipeline order. Each adjacent pair generates one handoff.
HANDOFF_SEQUENCE = [
    ("discovery", "design"),
    ("design", "develop"),
    ("develop", "deploy"),
    ("deploy", "pm"),
]

_HANDOFF_NUMBER = {pair: i + 1 for i, pair in enumerate(HANDOFF_SEQUENCE)}


def write_handoff(
    project_id: str,
    from_agent: str,
    to_agent: str,
    summary: str,
    decisions: list[str] | str,
    constraints: list[str] | str,
    open_risks: list[str] | str,
    artifact_paths: list[str] | None = None,
) -> str:
    """Write a numbered markdown handoff packet from one agent to the next."""
    pair = (from_agent, to_agent)
    if pair not in _HANDOFF_NUMBER:
        raise ValueError(
            f"Invalid handoff pair: {pair}. Must be one of {HANDOFF_SEQUENCE}"
        )
    n = _HANDOFF_NUMBER[pair]
    ensure_project_dirs(project_id)
    fname = f"{n}_{from_agent}-to-{to_agent}.md"
    path = project_handoffs_dir(project_id) / fname

    def _bullets(x: list[str] | str) -> str:
        if isinstance(x, str):
            return x.strip()
        return "\n".join(f"- {item}" for item in x)

    body = [
        f"# Handoff {n}: {from_agent} → {to_agent}",
        "",
        f"_Project: `{project_id}`_",
        "",
        "## Summary",
        summary.strip(),
        "",
        "## Key decisions to honor",
        _bullets(decisions),
        "",
        "## Constraints",
        _bullets(constraints),
        "",
        "## Open risks / unresolved questions",
        _bullets(open_risks),
    ]
    if artifact_paths:
        body += ["", "## Linked artifacts", "\n".join(f"- `{p}`" for p in artifact_paths)]
    path.write_text("\n".join(body) + "\n")
    return f"OK. Wrote handoff packet {fname} ({path.stat().st_size} bytes)."


def read_handoff(project_id: str, from_agent: str, to_agent: str) -> str:
    """Read a previously-written handoff packet."""
    pair = (from_agent, to_agent)
    if pair not in _HANDOFF_NUMBER:
        raise ValueError(f"Invalid handoff pair: {pair}")
    n = _HANDOFF_NUMBER[pair]
    fname = f"{n}_{from_agent}-to-{to_agent}.md"
    path = project_handoffs_dir(project_id) / fname
    if not path.exists():
        raise FileNotFoundError(f"Handoff not found: {path}")
    return path.read_text()


def all_handoffs(project_id: str) -> list[tuple[str, str]]:
    """Return list of (filename, content) tuples for all handoffs of a project, in order."""
    d = project_handoffs_dir(project_id)
    if not d.exists():
        return []
    out: list[tuple[str, str]] = []
    for n, (frm, to) in enumerate(HANDOFF_SEQUENCE, start=1):
        fname = f"{n}_{frm}-to-{to}.md"
        p = d / fname
        if p.exists():
            out.append((fname, p.read_text()))
    return out
