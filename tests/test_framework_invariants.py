"""The framework's central invariant: question ownership.

The mapping "q17-q26 belongs to Design" lives in three unconnected places —
framework/questions.json's phase fields, each agent's owned_question_ids range,
and prose in prompts/*.md. Inserting a question in the middle silently
desynchronizes all three, and the failure is invisible: the pipeline runs, the
PM agent finds blanks it has no mandate to fill, and the PRD ships incomplete.

These tests are the guard.
"""
from __future__ import annotations

import pytest

from agents.deploy import DEPLOY
from agents.design import DESIGN
from agents.develop import DEVELOP
from agents.discovery import DISCOVERY
from agents.intake import INTAKE
from agents.pm import PM
from framework import load_framework

SCOPED_AGENTS = [INTAKE, DISCOVERY, DESIGN, DEVELOP, DEPLOY]


@pytest.fixture(scope="module")
def framework():
    return load_framework()


@pytest.fixture(scope="module")
def all_qids(framework):
    return [q["id"] for s in framework["sections"] for q in s["questions"]]


def test_framework_has_57_contiguous_questions(all_qids):
    assert all_qids == [f"q{i}" for i in range(1, 58)]


def test_every_question_owned_by_exactly_one_agent(all_qids):
    owners: dict[str, list[str]] = {qid: [] for qid in all_qids}
    for agent in SCOPED_AGENTS:
        for qid in agent.owned_question_ids:
            assert qid in owners, f"{agent.name} claims {qid}, which is not in the framework"
            owners[qid].append(agent.name)

    unowned = [q for q, a in owners.items() if not a]
    contested = {q: a for q, a in owners.items() if len(a) > 1}
    assert not unowned, f"no agent owns: {unowned}"
    assert not contested, f"multiple agents own: {contested}"


def test_agent_ranges_match_questionsjson_phase_boundaries(framework):
    """Each scoped agent's range must be exactly the questions in its phase."""
    by_phase: dict[str, list[str]] = {}
    for section in framework["sections"]:
        by_phase.setdefault(section["phase"], []).extend(q["id"] for q in section["questions"])

    # Intake and Discovery split the first phase between them; the rest map 1:1.
    assert sorted(INTAKE.owned_question_ids + DISCOVERY.owned_question_ids) == sorted(
        by_phase["discovery"]
    ), "intake + discovery must jointly cover the discovery phase"

    for agent, phase in ((DESIGN, "design"), (DEVELOP, "develop"), (DEPLOY, "deploy")):
        assert sorted(agent.owned_question_ids) == sorted(
            by_phase[phase]
        ), f"{agent.name} range drifted from questions.json phase '{phase}'"


def test_pm_owns_nothing_but_can_edit_everything():
    """PM is the only cross-cutting agent; a scoped range would break that."""
    assert PM.owned_question_ids == []


class TestCompletionSemantics:
    """`needs-review` is a correct terminal state, not a failure. The prompts tell
    agents to use it when they've made an assumption a human should check, so
    counting it as unanswered cries wolf and makes resume redo finished work."""

    def _project(self, tmp_path, monkeypatch, statuses):
        import json

        from tools import question_io as qio

        target = tmp_path / "p.json"
        target.write_text(
            json.dumps(
                {
                    "version": 1,
                    "meta": {},
                    "data": {
                        qid: {"response": resp, "status": status}
                        for qid, (resp, status) in statuses.items()
                    },
                }
            )
        )
        monkeypatch.setattr(qio, "project_path", lambda _pid: target)
        return "p"

    def test_needs_review_counts_as_answered(self, tmp_path, monkeypatch):
        import orchestrator as o

        pid = self._project(
            tmp_path,
            monkeypatch,
            {q: ("real content", "needs-review") for q in INTAKE.owned_question_ids},
        )
        assert o._unanswered(INTAKE, pid) == []
        assert o._agent_done(INTAKE, pid) is True
        assert o._flagged(INTAKE, pid) == INTAKE.owned_question_ids

    def test_blank_response_is_unanswered_whatever_the_status(self, tmp_path, monkeypatch):
        import orchestrator as o

        statuses = {q: ("real content", "complete") for q in INTAKE.owned_question_ids}
        statuses["q3"] = ("   ", "complete")  # status lies; content is empty
        pid = self._project(tmp_path, monkeypatch, statuses)
        assert o._unanswered(INTAKE, pid) == ["q3"]
        assert o._agent_done(INTAKE, pid) is False


def test_handoff_chain_is_connected():
    """next_agent must form one unbroken chain ending at pm."""
    chain, seen = [], set()
    node = INTAKE
    by_name = {a.name: a for a in [*SCOPED_AGENTS, PM]}
    while node is not None:
        assert node.name not in seen, f"cycle in handoff chain at {node.name}"
        seen.add(node.name)
        chain.append(node.name)
        node = by_name.get(node.next_agent) if node.next_agent else None
    assert chain == ["intake", "discovery", "design", "develop", "deploy", "pm"]
