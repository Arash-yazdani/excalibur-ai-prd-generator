#!/usr/bin/env python3
"""CLI entry point for AI PM EXCALIBUR's six-agent pipeline.

Subcommands:
    run <project-id>       Kick off the full pipeline (intake → ... → pm).
                           Requires either q1-q7 already filled OR meta.intake_text
                           present (intake will populate q1-q7 from it).
    resume <project-id>    Resume after pause/interruption. Clears the pause flag,
                           skips agents whose owned questions are all complete.
    reflect <project-id>   Reflection-only pass (each agent appends lessons learned).
    only <agent>           Run a single agent (for iteration). e.g. `only design`.
    pause <project-id>     Set the soft-pause flag. The currently-running pipeline
                           will exit cleanly between agents.
    intake <project-id>    Run only the intake step (populate q1-q7 from intake_text).

Pipeline order: intake → discovery → design → develop → deploy → pm → reflect-all

Auth: invokes the bundled Claude Code CLI from claude-agent-sdk, which inherits
the user's `claude auth login` OAuth (Claude Max). No ANTHROPIC_API_KEY required.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env (PROJECTS_DIR override etc.) from this dir.
load_dotenv(Path(__file__).parent / ".env", override=True)

# Force the bundled Claude Code CLI onto its persistent OAuth token (Claude
# Max subscription). Two classes of env vars can derail this:
#
#   1. ANTHROPIC_API_KEY (even an empty string) makes the CLI prefer API-key
#      auth over OAuth. Stripping it forces OAuth precedence.
#   2. The CLAUDE_CODE_* host-injection vars tell the CLI "expect IPC-mediated
#      auth from your parent host (Claude Desktop)". Outside Claude Desktop's
#      IPC scope — which we are, as a Python server spawned independently —
#      that handshake fails and the CLI 401s instead of falling through to its
#      persistent OAuth token. Stripping them lets the CLI use the long-lived
#      token from `claude setup-token`.
#
# Prerequisite: the user must have run `claude setup-token` once to create the
# persistent on-disk OAuth credential. `claude auth login` alone is not
# sufficient when the parent shell is Claude Desktop, because Desktop manages
# auth via env injection rather than persisting credentials to disk.
for _var in (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDECODE",
):
    os.environ.pop(_var, None)

from agents.base import (  # noqa: E402
    AgentConfig,
    PipelinePaused,
    check_pause_flag,
    reflect,
    run_agent,
)
from agents.intake import INTAKE  # noqa: E402
from agents.discovery import DISCOVERY  # noqa: E402
from tools import question_io as qio  # noqa: E402
from tools import runs as runs_log  # noqa: E402
from tools.auth_preflight import check_auth_ready as _check_auth_ready  # noqa: E402
from tools.paths import ensure_project_dirs, pause_flag_path  # noqa: E402

# Pipeline: intake first, then the five consultants.
PIPELINE: list[AgentConfig] = [INTAKE, DISCOVERY]
try:
    from agents.design import DESIGN  # type: ignore
    PIPELINE.append(DESIGN)
except ImportError:
    pass
try:
    from agents.develop import DEVELOP  # type: ignore
    PIPELINE.append(DEVELOP)
except ImportError:
    pass
try:
    from agents.deploy import DEPLOY  # type: ignore
    PIPELINE.append(DEPLOY)
except ImportError:
    pass
try:
    from agents.pm import PM  # type: ignore
    PIPELINE.append(PM)
except ImportError:
    pass


def _check_intake_ready(project_id: str) -> tuple[bool, str]:
    """Pre-flight: pipeline can start if EITHER q1-q7 are all complete already
    (legacy / manual entry) OR meta.intake_text has content (intake will fill them)."""
    project = qio.load_project(project_id)
    q1_q7_complete = all(
        (qio.get_response(project, f"q{i}")[1] == "complete"
         and qio.get_response(project, f"q{i}")[0].strip())
        for i in range(1, 8)
    )
    has_intake_text = bool(project.get("meta", {}).get("intake_text", "").strip())
    if q1_q7_complete:
        return True, "q1-q7 already filled — intake will skip"
    if has_intake_text:
        return True, "intake_text present — intake agent will populate q1-q7"
    return False, "no intake_text and q1-q7 not filled — paste context in the UI first"


def _agent_done(agent: AgentConfig, project_id: str) -> bool:
    """An agent is 'done' if every question it owns has status=complete with non-empty response."""
    if not agent.owned_question_ids:
        return False  # PM has no scoped questions; never auto-skip
    project = qio.load_project(project_id)
    for qid in agent.owned_question_ids:
        response, status = qio.get_response(project, qid)
        if status != "complete" or not response.strip():
            return False
    return True


async def cmd_run(project_id: str, *, resume: bool = False) -> int:
    print(f"\nLoading project: {project_id}")
    ensure_project_dirs(project_id)

    # Resume mode: clear any pre-existing pause flag.
    pf = pause_flag_path(project_id)
    if resume and pf.exists():
        pf.unlink()
        print("  cleared pause flag")

    auth_ok, auth_reason = _check_auth_ready()
    print(f"  pre-flight (auth): {auth_reason}")
    if not auth_ok:
        print(f"\nERROR: {auth_reason}")
        runs_log.log_event(project_id, "orchestrator", "auth_failed", reason=auth_reason)
        return 2

    ok, reason = _check_intake_ready(project_id)
    print(f"  pre-flight (intake): {reason}")
    if not ok:
        print(f"\nERROR: {reason}")
        return 2

    runs_log.log_event(
        project_id, "orchestrator", "pipeline_start" if not resume else "pipeline_resumed",
        agents=[a.name for a in PIPELINE],
    )

    try:
        for agent in PIPELINE:
            check_pause_flag(project_id)  # exits cleanly between agents if flag set
            if resume and _agent_done(agent, project_id):
                print(f"\n[skip] {agent.name} — work already complete (resume mode).")
                continue
            try:
                await run_agent(agent, project_id)
            except PipelinePaused:
                raise
            except Exception as e:
                runs_log.log_event(project_id, agent.name, "error", error=str(e))
                print(f"\nERROR in {agent.name}: {e}")
                return 1
    except PipelinePaused:
        runs_log.log_event(project_id, "orchestrator", "pipeline_paused")
        print("\n⏸  Pipeline paused. Resume with `python orchestrator.py resume <pid>`.")
        return 0

    runs_log.log_event(project_id, "orchestrator", "pipeline_complete")

    print("\n--- Reflection pass (each agent appends lessons learned) ---")
    for agent in PIPELINE:
        try:
            check_pause_flag(project_id)
            await reflect(agent, project_id)
        except PipelinePaused:
            print("  reflection paused (pause flag detected).")
            break
        except Exception as e:
            runs_log.log_event(project_id, agent.name, "reflection_error", error=str(e))
            print(f"  reflection failed for {agent.name}: {e}")

    events = runs_log.read_events(project_id)
    completes = [e for e in events if e.get("event") == "agent_complete"]
    total_cost = sum(float(e.get("total_cost_usd") or 0.0) for e in completes)
    total_in = sum(int(e.get("input_tokens") or 0) for e in completes)
    total_out = sum(int(e.get("output_tokens") or 0) for e in completes)

    print(f"\n✓ Pipeline complete for {project_id}.")
    print(f"  - Project JSON: {qio.project_path(project_id)}")
    print(f"  - Run log: {runs_log.RUNS_LOG}")
    print(f"  - Equivalent API cost: ${total_cost:.2f}  (Max subscription, no actual charge)")
    print(f"  - Token usage: {total_in:,} input / {total_out:,} output")
    return 0


async def cmd_reflect(project_id: str) -> int:
    for agent in PIPELINE:
        if not _agent_done(agent, project_id) and agent.owned_question_ids:
            print(f"  [skip] {agent.name} — its questions aren't all complete yet.")
            continue
        try:
            await reflect(agent, project_id)
            print(f"  ✓ reflection appended for {agent.name}")
        except Exception as e:
            print(f"  ✗ {agent.name}: {e}")
    return 0


async def cmd_only(agent_name: str, project_id: str) -> int:
    matching = [a for a in PIPELINE if a.name == agent_name]
    if not matching:
        print(f"ERROR: unknown agent {agent_name!r}. Available: {[a.name for a in PIPELINE]}")
        return 2
    ensure_project_dirs(project_id)
    auth_ok, auth_reason = _check_auth_ready()
    print(f"  pre-flight (auth): {auth_reason}")
    if not auth_ok:
        print(f"\nERROR: {auth_reason}")
        runs_log.log_event(project_id, "orchestrator", "auth_failed", reason=auth_reason)
        return 2
    await run_agent(matching[0], project_id)
    return 0


def cmd_pause(project_id: str) -> int:
    """Set the soft-pause flag. A running pipeline will detect it between agents
    and exit cleanly. If no pipeline is running, the flag persists until a
    `resume` clears it."""
    ensure_project_dirs(project_id)
    pf = pause_flag_path(project_id)
    pf.write_text("paused\n")
    runs_log.log_event(project_id, "orchestrator", "pause_requested")
    print(f"⏸  Pause flag set: {pf}")
    print("   The running pipeline will exit between agents. Use `resume` to continue.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI PM EXCALIBUR — six-agent pipeline orchestrator")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run the full pipeline")
    p_run.add_argument("project_id")

    p_resume = sub.add_parser("resume", help="Resume after pause (clears pause flag, skips done agents)")
    p_resume.add_argument("project_id")

    p_reflect = sub.add_parser("reflect", help="Reflection-only pass for all agents")
    p_reflect.add_argument("project_id")

    p_only = sub.add_parser("only", help="Run a single agent (debug / iteration)")
    p_only.add_argument("agent_name")
    p_only.add_argument("project_id")

    p_intake = sub.add_parser("intake", help="Run only the intake agent (populate q1-q7 from intake_text)")
    p_intake.add_argument("project_id")

    p_pause = sub.add_parser("pause", help="Set the soft-pause flag (running pipeline exits between agents)")
    p_pause.add_argument("project_id")

    args = parser.parse_args(argv)

    if args.cmd == "run":
        return asyncio.run(cmd_run(args.project_id, resume=False))
    if args.cmd == "resume":
        return asyncio.run(cmd_run(args.project_id, resume=True))
    if args.cmd == "reflect":
        return asyncio.run(cmd_reflect(args.project_id))
    if args.cmd == "only":
        return asyncio.run(cmd_only(args.agent_name, args.project_id))
    if args.cmd == "intake":
        return asyncio.run(cmd_only("intake", args.project_id))
    if args.cmd == "pause":
        return cmd_pause(args.project_id)
    return 2


if __name__ == "__main__":
    sys.exit(main())
