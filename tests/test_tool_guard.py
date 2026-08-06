"""Malformed tool input must come back as a readable error, not a KeyError.

Observed for real: on a discovery run, the model emitted invalid JSON for
`write_handoff` and the SDK delivered `{"__unparsedToolInput": {...}}`. The
handler indexes `args["summary"]`, which raised KeyError — escaping the
_ok/_err contract the whole tool layer depends on. The model happened to
recover that time. write_handoff is the only structural link between two
agents, so "happened to" is not good enough.
"""
from __future__ import annotations

import pytest

from agents.base import _guard


def _text(result: dict) -> str:
    return result["content"][0]["text"]


@pytest.fixture
def handler():
    @_guard("summary", "decisions")
    async def tool(args):
        return {"content": [{"type": "text", "text": f"ok:{args['summary']}"}]}

    return tool


class TestGuard:
    async def test_unparsed_input_returns_error_not_keyerror(self, handler):
        result = await handler({"__unparsedToolInput": {"raw": '{"summary": "broken'}})
        assert result["isError"] is True
        text = _text(result)
        assert "not valid JSON" in text
        assert "summary" in text and "decisions" in text

    async def test_missing_field_names_what_is_missing(self, handler):
        result = await handler({"summary": "here"})
        assert result["isError"] is True
        assert "decisions" in _text(result)

    async def test_empty_string_counts_as_missing(self, handler):
        result = await handler({"summary": "", "decisions": ["a"]})
        assert result["isError"] is True
        assert "summary" in _text(result)

    async def test_valid_input_passes_through(self, handler):
        result = await handler({"summary": "all good", "decisions": ["a"]})
        assert "isError" not in result
        assert _text(result) == "ok:all good"

    async def test_guard_never_raises(self, handler):
        """Whatever the model sends, the handler returns a dict."""
        for junk in ({}, {"__unparsedToolInput": None}, {"unexpected": 1}, {"summary": None}):
            result = await handler(junk)
            assert isinstance(result, dict)
            assert result.get("isError") is True


class TestEveryToolIsGuarded:
    """A new tool that indexes args without a guard reintroduces the bug."""

    def test_no_unguarded_arg_indexing(self):
        import inspect
        import re

        from agents import base

        src = inspect.getsource(base)
        # Every `async def <tool>(args: ...)` that indexes args must carry @_guard.
        for match in re.finditer(
            r"(@_guard\([^)]*\)\n\s*)?async def (\w+)\(args: dict\[str, Any\]\)"
            r" -> dict\[str, Any\]:\n(.*?)(?=\n    @tool|\n    tools_list|\n    return|\Z)",
            src,
            re.S,
        ):
            guarded, name, body = match.groups()
            if 'args["' in body and not guarded:
                pytest.fail(
                    f"{name}() indexes args directly without @_guard — malformed "
                    f"model input will raise KeyError instead of returning _err"
                )
