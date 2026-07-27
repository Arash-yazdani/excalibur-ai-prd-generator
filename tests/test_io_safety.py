"""Deterministic IO surface: markdown sanitization, path validation, atomic writes."""
from __future__ import annotations

import json

import pytest

from server import _safe_id, _slugify, render_markdown
from tools import artifacts as art_tools
from tools import handoff as handoff_tools


class TestMarkdownSanitization:
    """Agent-authored markdown reaches the SPA via innerHTML, and its content
    derives from untrusted pasted intake text."""

    @pytest.mark.parametrize(
        "payload",
        [
            "<script>fetch('/api/projects')</script>",
            "<img src=x onerror=alert(1)>",
            "[click](javascript:alert(1))",
            "<iframe src='//evil.example'></iframe>",
            "<div onclick='alert(1)'>hi</div>",
            "<svg><animate onbegin=alert(1)>",
        ],
    )
    def test_dangerous_html_is_stripped(self, payload):
        out = render_markdown(payload).lower()
        for token in ("<script", "onerror", "javascript:", "<iframe", "onclick", "onbegin"):
            assert token not in out

    def test_legitimate_markdown_survives(self):
        out = render_markdown(
            "# Title\n\n**bold** `code`\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
            "- item\n\n```py\nx = 1\n```\n\n[link](https://example.com)"
        )
        for tag in ("<h1", "<strong", "<code", "<table", "<li", "<pre", "https://example.com"):
            assert tag in out


class TestIdValidation:
    @pytest.mark.parametrize("pid", ["ok-id", "OK_ID", "a1"])
    def test_accepts_clean_ids(self, pid):
        assert _safe_id(pid)

    @pytest.mark.parametrize("pid", ["../etc", "a/b", "a b", "", "x" * 200, "a.b"])
    def test_rejects_traversal_and_junk(self, pid):
        assert not _safe_id(pid)

    def test_slugify_strips_path_characters(self):
        assert _slugify("My Project / v2!") == "my-project-v2"
        assert _slugify("!!!") == "untitled"


class TestArtifactPaths:
    """The model supplies these names, so read must validate as strictly as write."""

    @pytest.mark.parametrize("name", ["../../../etc/hosts", "a/b", "..", "x y"])
    def test_read_rejects_traversal(self, name):
        with pytest.raises(ValueError):
            art_tools.read_artifact("someproject", name)

    @pytest.mark.parametrize("name", ["../../../etc/hosts", "a/b"])
    def test_save_rejects_traversal(self, name):
        with pytest.raises(ValueError):
            art_tools.save_artifact("someproject", name, "content")


class TestHandoffSequence:
    """Handoffs are the only channel between agents; a wrong pair means an agent
    reads a packet that was never written for it."""

    PACKET = dict(summary="s", decisions=["d"], constraints=["c"], open_risks=["r"])

    @pytest.mark.parametrize(
        ("frm", "to"),
        [("deploy", "discovery"), ("discovery", "develop"), ("pm", "intake"), ("design", "design")],
    )
    def test_rejects_pairs_outside_the_pipeline_order(self, frm, to):
        with pytest.raises(ValueError):
            handoff_tools.write_handoff("p", frm, to, **self.PACKET)

    @pytest.mark.parametrize(("frm", "to"), handoff_tools.HANDOFF_SEQUENCE)
    def test_accepts_every_declared_pair(self, tmp_path, monkeypatch, frm, to):
        monkeypatch.setattr(handoff_tools, "project_handoffs_dir", lambda _pid: tmp_path)
        monkeypatch.setattr(handoff_tools, "ensure_project_dirs", lambda _pid: None)
        handoff_tools.write_handoff("p", frm, to, **self.PACKET)
        written = list(tmp_path.glob(f"*_{frm}-to-{to}.md"))
        assert len(written) == 1
        assert "## Summary" in written[0].read_text()


class TestAtomicProjectWrite:
    def test_save_project_leaves_no_partial_file(self, tmp_path, monkeypatch):
        """Cancel SIGTERMs the orchestrator mid-run; a truncated write would lose
        every answer in the pipeline."""
        from tools import question_io as qio

        target = tmp_path / "proj.json"
        monkeypatch.setattr(qio, "project_path", lambda _pid: target)

        qio.save_project("p", {"meta": {"name": "one"}, "data": {}})
        assert json.loads(target.read_text())["meta"]["name"] == "one"

        qio.save_project("p", {"meta": {"name": "two"}, "data": {"q1": {"response": "x"}}})
        reloaded = json.loads(target.read_text())
        assert reloaded["meta"]["name"] == "two"
        assert reloaded["data"]["q1"]["response"] == "x"

        # No temp files left behind.
        assert list(tmp_path.glob("*.tmp")) == []
