"""ConsultantAgent, base class for all six pipeline agents.

Each agent runs as a `ClaudeSDKClient` session that invokes the bundled
Claude Code CLI. Auth and model routing are whatever the environment provides
API key, gateway, cloud provider, or subscription, resolved by the CLI itself.
See tools/auth_preflight.py.

Per agent:
- A static system prompt assembled from persona + framework spec + lessons.
  (Delivered as a single string. Claude Code's harness handles caching
  internally; the SDK's system_prompt field doesn't accept cache_control
  blocks anyway.)
- A small set of typed tools wrapped as an in-process MCP server. The Intake
  agent gets a smaller surface (read_intake_text, set_meta, write_answer);
  the five consultant agents get the full eight-tool surface.
- One initial user message that scopes the work.

References:
- claude-agent-sdk: https://github.com/anthropics/claude-agent-sdk-python
- ClaudeAgentOptions field reference: src/claude_agent_sdk/types.py
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)

from framework import load_framework
from tools import artifacts as art_tools
from tools import handoff as handoff_tools
from tools import memory as memory_tools
from tools import question_io as qio
from tools import runs as runs_log
from tools.auth_preflight import resolve_model
from tools.paths import BASE_DIR, PROMPTS_DIR, pause_flag_path

# Model is overridable without touching code, set EXCALIBUR_MODEL (or the standard
# ANTHROPIC_MODEL) in .env. Deliberately no hardcoded default: an ID that is valid on
# the Anthropic API is wrong on Bedrock (inference profile ARN), Vertex (version name),
# and Foundry (deployment name). When unset we omit `model` entirely and let the CLI
# resolve its own default for whichever provider is configured.
# Shared with the preflight so it probes the model the pipeline actually runs on.
MODEL = resolve_model()
def _opt_out(var: str, default: str) -> str | None:
    """Env override that can also be switched off entirely with none/off/empty."""
    value = os.environ.get(var, default).strip().lower()
    return None if value in ("", "none", "off") else value


# `thinking` and `effort` are not universally supported: Haiku 4.5 and older models
# reject `effort`, and most gateway shims reject `thinking: adaptive`. Both are
# omitted from the request when set to none/off, so cheaper models, and anything
# behind a proxy, can still drive the pipeline.
#   EXCALIBUR_EFFORT=none EXCALIBUR_THINKING=none
DEFAULT_EFFORT = _opt_out("EXCALIBUR_EFFORT", "high")
# Reflection is a one-sentence task; it never needs the main loop's depth.
REFLECT_EFFORT = "medium" if DEFAULT_EFFORT else None
THINKING = {"type": "adaptive"} if _opt_out("EXCALIBUR_THINKING", "adaptive") else None
MCP_SERVER_KEY = "prd"  # dict key in mcp_servers; tools become mcp__prd__<name>
MAX_TURNS = 80


class PipelinePaused(Exception):
    """Raised when the orchestrator detects the pause flag between agents.
    Caught at the orchestrator level to exit cleanly with `pipeline_paused`
    state. Resume re-runs `python orchestrator.py resume <pid>` which skips
    already-complete agents and continues."""


@dataclass
class AgentConfig:
    """Static configuration for one agent in the pipeline."""

    name: str  # "discovery" | "design" | "develop" | "deploy" | "pm"
    role_label: str
    owned_question_ids: list[str]
    next_agent: str | None
    artifact_name: str | None = None
    # Built-in Claude Code tool names (e.g. "WebSearch") to allowlist for this
    # agent. Use sparingly, added as bare names to ClaudeAgentOptions.allowed_tools.
    extra_builtin_tools: list[str] = field(default_factory=list)


def load_prompt(agent_name: str) -> str:
    path = PROMPTS_DIR / f"{agent_name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Missing prompt file: {path}")
    return path.read_text()


def _format_framework_spec() -> str:
    fw = load_framework()
    lines = ["# Agentic PRD Framework", ""]
    for phase in fw["phases"]:
        lines.append(f"## {phase['icon']}. {phase['label']}")
        for s in fw["sections"]:
            if s["phase"] != phase["id"]:
                continue
            lines.append(f"### {s['title']} ({s['id']})")
            lines.append(f"_{s['subtitle']}_")
            for q in s["questions"]:
                lines.append(f"- **{q['id']}** ({q['theme']} / {q['topic']}): {q['q']}")
                lines.append(f"  - Tip: {q['tip']}")
            lines.append("")
    return "\n".join(lines)


def _build_system_prompt_str(agent_name: str) -> str:
    """Assemble the system prompt as a single string."""
    persona = load_prompt(agent_name)
    framework_spec = _format_framework_spec()
    lessons = memory_tools.read_lessons(agent_name)
    return "\n\n".join(
        [
            persona,
            "---",
            "# Framework Reference",
            framework_spec,
            "---",
            "# Cross-project Lessons (your own, from prior runs)",
            lessons,
            "_Apply these only when relevant. They are heuristics, not rules._",
        ]
    )


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _build_tools_server(project_id: str, cfg: AgentConfig):
    """Dispatch to the right tool surface for this agent.

    The Intake Agent has a smaller, different surface (read_intake_text,
    set_meta, write_answer). The five consultant agents share the same eight
    tools. Returns (server, allowlist) where allowlist is the list of fully-
    prefixed mcp__prd__<name> strings for ClaudeAgentOptions.allowed_tools.
    """
    if cfg.name == "intake":
        return _build_intake_tools_server(project_id, cfg)
    return _build_consultant_tools_server(project_id, cfg)


def _build_intake_tools_server(project_id: str, cfg: AgentConfig):
    """Tools for the Intake Agent only: read freeform text, update meta, write q1-q7."""

    @tool(
        "read_intake_text",
        "Read the freeform context the human pasted into this project. "
        "This is your only structured input, everything you write to q1-q7 should be grounded in it.",
        {},
    )
    async def read_intake_text(args: dict[str, Any]) -> dict[str, Any]:
        try:
            text = qio.get_intake_text(project_id)
        except FileNotFoundError as e:
            return _err(str(e))
        if not text.strip():
            return _err(
                "Intake text is empty. The human hasn't pasted any context yet, "
                "you cannot do meaningful intake. Stop and report this in your closing message."
            )
        return _ok(f"# Freeform intake from human ({len(text)} chars)\n\n{text}")

    @tool(
        "set_meta",
        "Update one or more project meta fields (name, owner, industry, date). "
        "Pass only the fields you want to change. The human's existing values stay otherwise.",
        {"name": str, "owner": str, "industry": str, "date": str},
    )
    async def set_meta(args: dict[str, Any]) -> dict[str, Any]:
        # Drop None/empty so we don't overwrite with blanks
        fields = {k: v for k, v in args.items() if v}
        if not fields:
            return _ok("No meta fields supplied; nothing changed.")
        try:
            return _ok(qio.update_meta(project_id, **fields))
        except ValueError as e:
            return _err(str(e))

    @tool(
        "write_answer",
        "Save your answer to one of q1-q7. response is the full markdown body. "
        "status defaults to 'complete'; use 'needs-review' for genuine gaps where the "
        "intake text didn't supply enough info and you've made an explicit assumption.",
        {"qid": str, "response": str, "status": str},
    )
    async def write_answer(args: dict[str, Any]) -> dict[str, Any]:
        qid = args["qid"]
        response = args["response"]
        status = args.get("status") or "complete"
        if qid not in cfg.owned_question_ids:
            return _err(
                f"{qid} is not in your scope. Intake owns q1-q7 only "
                f"({cfg.owned_question_ids}). The remaining questions are filled in by "
                f"the consultant agents downstream."
            )
        try:
            return _ok(qio.write_answer(project_id, qid, response, status))  # type: ignore[arg-type]
        except (ValueError, FileNotFoundError) as e:
            return _err(f"Error writing {qid}: {e}")

    tools_list = [read_intake_text, set_meta, write_answer]
    server = create_sdk_mcp_server(name="prd_intake", version="1.0.0", tools=tools_list)
    tool_names = ["read_intake_text", "set_meta", "write_answer"]
    allowlist = [f"mcp__{MCP_SERVER_KEY}__{n}" for n in tool_names]
    return server, allowlist


def _build_consultant_tools_server(project_id: str, cfg: AgentConfig):
    """Eight-tool surface shared by Discovery, Design, Develop, Deploy, and PM."""

    @tool(
        "read_question",
        "Get the framework definition + current response for a single question. "
        "Returns the question text, theme, topic, tip, current status, and current response.",
        {"qid": str},
    )
    async def read_question(args: dict[str, Any]) -> dict[str, Any]:
        qid = args["qid"]
        fw = load_framework()
        try:
            section, q = next(
                (s, qq) for s in fw["sections"] for qq in s["questions"] if qq["id"] == qid
            )
        except StopIteration:
            return _err(f"Unknown question id {qid!r}.")
        try:
            project = qio.load_project(project_id)
        except FileNotFoundError as e:
            return _err(str(e))
        response, status = qio.get_response(project, qid)
        out = [
            f"## {qid}, {q['topic']} ({q['theme']})",
            f"**Section:** {section['title']} (in phase: {section['phase']})",
            f"**Question:** {q['q']}",
            f"**Tip:** {q['tip']}",
            f"**Current status:** {status}",
        ]
        if response.strip():
            out.append(f"**Current response:**\n{response}")
        else:
            out.append("**Current response:** _(empty)_")
        return _ok("\n".join(out))

    @tool(
        "read_section",
        "Get all questions + current responses for one section (e.g. 'market', 'users', 'ux', 'model', 'launch').",
        {"section_id": str},
    )
    async def read_section(args: dict[str, Any]) -> dict[str, Any]:
        section_id = args["section_id"]
        fw = load_framework()
        try:
            section = next(s for s in fw["sections"] if s["id"] == section_id)
        except StopIteration:
            return _err(f"Unknown section id {section_id!r}.")
        try:
            project = qio.load_project(project_id)
        except FileNotFoundError as e:
            return _err(str(e))
        return _ok(qio.render_section_for_agent(project, section))

    @tool(
        "read_phase",
        "Get every section's content for a whole phase. Pass 'discovery', 'design', 'develop', or 'deploy'.",
        {"phase_id": str},
    )
    async def read_phase(args: dict[str, Any]) -> dict[str, Any]:
        phase_id = args["phase_id"]
        fw = load_framework()
        if not any(p["id"] == phase_id for p in fw["phases"]):
            return _err(f"Unknown phase id {phase_id!r}.")
        try:
            project = qio.load_project(project_id)
        except FileNotFoundError as e:
            return _err(str(e))
        return _ok(qio.render_phase_for_agent(project, fw, phase_id))

    @tool(
        "write_answer",
        "Save your answer to a question. qid is required (e.g. 'q8'). response is the full markdown body. "
        "status defaults to 'complete'; use 'needs-review' if you've made an explicit assumption that warrants human review.",
        {"qid": str, "response": str, "status": str},
    )
    async def write_answer(args: dict[str, Any]) -> dict[str, Any]:
        qid = args["qid"]
        response = args["response"]
        status = args.get("status") or "complete"
        if cfg.name != "pm" and qid not in cfg.owned_question_ids:
            return _err(
                f"{qid} is not in your scope. You own these questions only: "
                f"{cfg.owned_question_ids}. Only the PM agent can edit any q1-q57."
            )
        try:
            return _ok(qio.write_answer(project_id, qid, response, status))  # type: ignore[arg-type]
        except (ValueError, FileNotFoundError) as e:
            return _err(f"Error writing {qid}: {e}")

    @tool(
        "save_artifact",
        "Save a stakeholder-shareable markdown artifact. name must be alphanumeric/dash/underscore only "
        "(e.g. 'design-artifact', 'develop-artifact', 'final-prd'). content is the full markdown body.",
        {"name": str, "content": str},
    )
    async def save_artifact(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(art_tools.save_artifact(project_id, args["name"], args["content"]))
        except ValueError as e:
            return _err(str(e))

    @tool(
        "read_artifact",
        "Read an artifact written by an earlier agent. Pass the artifact name without the .md extension.",
        {"name": str},
    )
    async def read_artifact(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(art_tools.read_artifact(project_id, args["name"]))
        except FileNotFoundError as e:
            return _err(str(e))

    @tool(
        "read_handoff_from",
        "Read the handoff packet from a prior agent. prior_agent must be one of: "
        "'discovery', 'design', 'develop', 'deploy'. You cannot read your own outgoing handoff.",
        {"prior_agent": str},
    )
    async def read_handoff_from(args: dict[str, Any]) -> dict[str, Any]:
        prior = args["prior_agent"]
        try:
            return _ok(handoff_tools.read_handoff(project_id, prior, cfg.name))
        except (ValueError, FileNotFoundError) as e:
            return _err(str(e))

    @tool(
        "write_handoff",
        "Write the handoff packet for the next agent. Call this exactly once at the end of your work. "
        "summary: 1-2 paragraphs in plain language. "
        "decisions: list of bullets the next agent must honor. "
        "constraints: list of external constraints (regulatory, infra, budget). "
        "open_risks: list of unresolved questions to flag honestly. "
        "artifact_paths: optional list of artifact filenames (without .md) you saved.",
        {
            "summary": str,
            "decisions": list,
            "constraints": list,
            "open_risks": list,
            "artifact_paths": list,
        },
    )
    async def write_handoff(args: dict[str, Any]) -> dict[str, Any]:
        if cfg.next_agent is None:
            return _err("This agent (PM) is the final stage. There is nobody to hand off to.")
        try:
            return _ok(
                handoff_tools.write_handoff(
                    project_id,
                    from_agent=cfg.name,
                    to_agent=cfg.next_agent,
                    summary=args["summary"],
                    decisions=args.get("decisions") or [],
                    constraints=args.get("constraints") or [],
                    open_risks=args.get("open_risks") or [],
                    artifact_paths=args.get("artifact_paths") or None,
                )
            )
        except ValueError as e:
            return _err(str(e))

    tools_list = [
        read_question,
        read_section,
        read_phase,
        write_answer,
        save_artifact,
        read_artifact,
        read_handoff_from,
        write_handoff,
    ]
    server = create_sdk_mcp_server(name="prd_tools", version="1.0.0", tools=tools_list)
    # Tool naming: each MCP tool exposed under mcp__<dict-key>__<tool-name>.
    # The decorator stores its name on the wrapped function via .name (or the
    # underlying SdkMcpTool object). We pull it pragmatically, the inner name
    # we passed to @tool above is the source of truth.
    tool_names = [
        "read_question",
        "read_section",
        "read_phase",
        "write_answer",
        "save_artifact",
        "read_artifact",
        "read_handoff_from",
        "write_handoff",
    ]
    allowlist = [f"mcp__{MCP_SERVER_KEY}__{n}" for n in tool_names]
    return server, allowlist


def _build_initial_user_message(project_id: str, cfg: AgentConfig) -> str:
    project = qio.load_project(project_id)
    meta = project.get("meta", {})

    parts = [
        f"You are working on project `{project_id}`.",
        "",
        "## Project meta",
        f"- Name: {meta.get('name', '?')}",
        f"- Owner: {meta.get('owner', '?')}",
        f"- Industry: {meta.get('industry', '?')}",
        f"- Date: {meta.get('date', '?')}",
        "",
        "## Your scope",
    ]

    if cfg.name == "intake":
        parts.append(
            "Read the freeform intake text the human pasted (call `read_intake_text`), "
            "optionally update meta fields you can extract from it (`set_meta`), and "
            "structure the input into q1-q7 by calling `write_answer` for each. "
            "Mark genuine gaps as `needs-review`. Do NOT call `write_handoff`: "
            "Discovery picks up directly from your q1-q7 answers."
        )
    elif cfg.owned_question_ids:
        parts.append(
            f"You own questions {cfg.owned_question_ids[0]}–{cfg.owned_question_ids[-1]} "
            f"({len(cfg.owned_question_ids)} questions total). Complete them in order, "
            f"calling `write_answer` for each. Use `read_section`/`read_phase` first to "
            f"ground your responses in the prior phases' work."
        )
    else:
        parts.append(
            "You are the AI PM Manager. You don't own any specific questions, instead, "
            "you review the entire PRD, sense-check it, fix inconsistencies via "
            "`write_answer`, and produce the final consolidated `final-prd` artifact."
        )

    if cfg.next_agent and cfg.name != "intake":
        parts.append(
            f"\nWhen you're done, call `write_handoff` exactly once with the "
            f"packet for the {cfg.next_agent} agent."
        )

    if cfg.artifact_name:
        parts.append(
            f"\nBefore handing off, save a stakeholder-shareable artifact named "
            f"`{cfg.artifact_name}` via `save_artifact`."
        )

    if cfg.name == "intake":
        parts.append(
            "\n## How to work\n"
            "1. `read_intake_text` to load the human's freeform input.\n"
            "2. (Optional) `set_meta` with name/industry/date you can extract.\n"
            "3. For q1 through q7 in order, call `write_answer` with your structured response.\n"
            "4. End with a 1-paragraph summary of what you understood and any gaps. No handoff packet.\n\n"
            "Quality bar: ground every claim in the freeform input. Mark genuine gaps as "
            "needs-review rather than inventing market sizing or competitor names."
        )
    else:
        parts.append(
            "\n## How to work\n"
            "1. Start by reading the prior phases' work (use `read_phase` or `read_section`).\n"
            "2. If a prior handoff packet exists, read it (`read_handoff_from`).\n"
            "3. Answer your scoped questions one at a time via `write_answer`.\n"
            "4. Save your stakeholder artifact (if you produce one).\n"
            "5. Write your handoff packet (if you have a successor).\n\n"
            "Quality bar: an answer should be substantive enough that a senior PM at a "
            "Fortune 500 would accept it without rework. Brief but complete. Cite "
            "specific facts from prior phases rather than generalizing. Flag genuine "
            "uncertainty as `needs-review` rather than fabricating confidence."
        )

    return "\n".join(parts)


def check_pause_flag(project_id: str) -> None:
    """Raise PipelinePaused if the orchestrator's pause flag exists for this project.
    Called from the orchestrator loop between agents (NOT inside run_agent, once
    an agent has started, it runs to completion). Soft pause means: finish the
    current agent, then exit cleanly before the next one starts."""
    if pause_flag_path(project_id).exists():
        raise PipelinePaused(f"pause flag set for project {project_id}")


def _accumulate_usage(running: dict[str, int], usage: dict[str, Any] | None) -> None:
    if not usage:
        return
    running["input_tokens"] += int(usage.get("input_tokens") or 0)
    running["output_tokens"] += int(usage.get("output_tokens") or 0)
    running["cache_read_input_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
    running["cache_creation_input_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)


async def run_agent(
    cfg: AgentConfig,
    project_id: str,
    *,
    effort: str = DEFAULT_EFFORT,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run one consultant agent end-to-end. Returns a usage summary."""
    system_prompt = _build_system_prompt_str(cfg.name)
    server, custom_allowlist = _build_tools_server(project_id, cfg)
    initial_user = _build_initial_user_message(project_id, cfg)

    runs_log.log_event(project_id, cfg.name, "agent_start", role=cfg.role_label)

    if verbose:
        print(f"\n{'='*70}")
        print(f"  ► {cfg.role_label} ({cfg.name}) starting on {project_id}")
        print(f"{'='*70}")

    options = ClaudeAgentOptions(
        model=MODEL,
        thinking=THINKING,  # type: ignore[arg-type]
        effort=effort,  # type: ignore[arg-type]
        system_prompt=system_prompt,
        mcp_servers={MCP_SERVER_KEY: server},
        allowed_tools=custom_allowlist + list(cfg.extra_builtin_tools),
        max_turns=MAX_TURNS,
        cwd=str(BASE_DIR),
        setting_sources=None,  # do not auto-load user/project CLAUDE.md or settings.json
        permission_mode="acceptEdits",
    )

    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    last_stop_reason: str | None = None
    last_cost: float | None = None

    async with ClaudeSDKClient(options=options) as client:
        await client.query(initial_user)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                _accumulate_usage(usage, msg.usage)
                if verbose:
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            text = block.text or ""
                            if text.strip():
                                print(f"\n[{cfg.name}] {text.strip()[:600]}")
                        elif isinstance(block, ToolUseBlock):
                            snippet = str(block.input)[:120]
                            ellipsis = "…" if len(str(block.input)) > 120 else ""
                            print(f"  → tool: {block.name}({snippet}{ellipsis})")
            elif isinstance(msg, ResultMessage):
                last_stop_reason = msg.stop_reason
                last_cost = msg.total_cost_usd
                # ResultMessage carries the run's authoritative totals. Summing
                # per-AssistantMessage usage undercounts badly, a run that wrote
                # 4K characters reported 160 in / 29 out, because most messages
                # in the CLI harness don't carry usage at all. Prefer this.
                if msg.usage:
                    for key in usage:
                        reported = msg.usage.get(key)
                        if reported:
                            usage[key] = int(reported)
                if msg.is_error:
                    print(f"\n[{cfg.name}] !! result reports error (api_error_status={msg.api_error_status})")

    runs_log.log_event(
        project_id,
        cfg.name,
        "agent_complete",
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cache_read=usage["cache_read_input_tokens"],
        cache_create=usage["cache_creation_input_tokens"],
        stop_reason=last_stop_reason,
        # None on subscription auth (no per-token charge); a real charge otherwise.
        total_cost_usd=last_cost or 0.0,
    )

    # Abort hard if the agent produced zero tokens. This catches silent auth
    # failure, without it, the pipeline marches through dead handoffs and
    # produces a final PRD that's actually empty.
    if usage["output_tokens"] == 0 and usage["input_tokens"] == 0:
        raise RuntimeError(
            f"{cfg.name} produced zero tokens, which means the model call failed "
            f"(usually auth). Run `python tools/auth_preflight.py` to see which "
            f"credential is detected and whether it works, then restart the server."
        )

    # max_turns exhaustion is not an error to the SDK, the session just stops. Left
    # unchecked, the agent logs `agent_complete` with half its questions unanswered
    # and the pipeline advances. Surface it; the orchestrator asserts completeness.
    if last_stop_reason == "max_turns":
        print(
            f"\n[{cfg.name}] !! hit the {MAX_TURNS}-turn limit, its questions may be "
            f"incomplete. Re-run this agent with `orchestrator.py only {cfg.name} <pid>`."
        )

    return {
        "stop_reason": last_stop_reason,
        **usage,
        "total_cost_usd": last_cost or 0.0,
    }


async def reflect(cfg: AgentConfig, project_id: str) -> dict[str, Any]:
    """Post-run reflection: ask the agent to append 1-3 lessons to its lessons file."""
    project = qio.load_project(project_id)

    @tool(
        "append_lesson",
        "Append one concise lesson (≤ 50 words) to your cross-project memory. "
        "The lesson should be project-agnostic, actionable on any future project, "
        "not facts specific to this one.",
        {"lesson": str},
    )
    async def append_lesson(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(memory_tools.append_lesson(cfg.name, args["lesson"], project_id=project_id))
        except ValueError as e:
            return _err(str(e))

    server = create_sdk_mcp_server(name="reflection", version="1.0.0", tools=[append_lesson])

    initial = (
        f"You just completed your work on project `{project_id}`. The full project "
        f"is now finalized.\n\n"
        f"Project meta: {project.get('meta', {})}\n\n"
        f"Reflect on what you learned. Append 1–3 concise lessons (≤ 50 words each) "
        f"to your cross-project memory via `append_lesson`. Each lesson should be "
        f"actionable and project-agnostic, heuristics you'd want to remember on "
        f"your next project of any kind, not facts specific to this one. "
        f"After appending the lessons, end your turn."
    )

    runs_log.log_event(project_id, cfg.name, "reflection_start")

    options = ClaudeAgentOptions(
        model=MODEL,
        thinking=THINKING,  # type: ignore[arg-type]
        effort=REFLECT_EFFORT,  # type: ignore[arg-type]
        system_prompt=_build_system_prompt_str(cfg.name),
        mcp_servers={"reflect": server},
        allowed_tools=["mcp__reflect__append_lesson"],
        max_turns=4,
        cwd=str(BASE_DIR),
        setting_sources=None,
        permission_mode="acceptEdits",
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(initial)
        async for _ in client.receive_response():
            pass

    runs_log.log_event(project_id, cfg.name, "reflection_complete")
    return {"ok": True}
