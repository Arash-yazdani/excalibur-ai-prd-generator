"""Agentic PRD framework registry.

`questions.json` is the single source of truth for phases, sections, and the
57 questions. The web UI and every agent read from it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FRAMEWORK_PATH = Path(__file__).parent / "questions.json"


def load_framework() -> dict[str, Any]:
    """Return the full framework dict (phases, sections, questions)."""
    return json.loads(_FRAMEWORK_PATH.read_text())


def get_phase(framework: dict[str, Any], phase_id: str) -> dict[str, Any]:
    for p in framework["phases"]:
        if p["id"] == phase_id:
            return p
    raise KeyError(f"Unknown phase: {phase_id}")


def get_section(framework: dict[str, Any], section_id: str) -> dict[str, Any]:
    for s in framework["sections"]:
        if s["id"] == section_id:
            return s
    raise KeyError(f"Unknown section: {section_id}")


def get_question(framework: dict[str, Any], qid: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (section, question) for a given question id."""
    for s in framework["sections"]:
        for q in s["questions"]:
            if q["id"] == qid:
                return s, q
    raise KeyError(f"Unknown question: {qid}")


def sections_in_phase(framework: dict[str, Any], phase_id: str) -> list[dict[str, Any]]:
    return [s for s in framework["sections"] if s["phase"] == phase_id]


def questions_in_section(framework: dict[str, Any], section_id: str) -> list[dict[str, Any]]:
    return get_section(framework, section_id)["questions"]


def questions_in_phase(framework: dict[str, Any], phase_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in sections_in_phase(framework, phase_id):
        out.extend(s["questions"])
    return out
