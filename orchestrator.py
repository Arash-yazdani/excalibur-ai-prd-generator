#!/usr/bin/env python3
"""CLI entry point for EXCALIBUR's six-agent pipeline.

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

Auth: invokes the bundled Claude Code CLI from claude-agent-sdk, which uses
whichever credential your environment provides, an Anthropic API key, a gateway
token, a cloud-provider switch (Bedrock/Vertex/Foundry), or a Claude subscription.
See tools/auth_preflight.py and the Providers table in the README.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env (PROJECTS_DIR override etc.) from this dir.
load_dotenv(Path(__file__).parent / ".env", override=True)

# Strip the CLAUDE_CODE_* host-IPC vars before importing the agent layer, they
# make the CLI expect IPC auth from Claude Desktop and 401 when spawned standalone.
# The user's credential is left untouched; tools/auth_preflight detects it.
from tools.auth_preflight import strip_host_ipc_env  # noqa: E402

strip_host_ipc_env()

from agents.base import (  # noqa: E402
    AgentConfig,
    PipelinePaused,
    check_pause_flag,
    reflect,
    run_agent,
)
from agents.deploy import DEPLOY  # noqa: E402
from agents.design import DESIGN  # noqa: E402
from agents.develop import DEVELOP  # noqa: E402
from agents.discovery import DISCOVERY  # noqa: E402
from agents.intake import INTAKE  # noqa: E402
from agents.pm import PM  # noqa: E402
from tools import question_io as qio  # noqa: E402
from tools import runs as runs_log  # noqa: E402
from tools.auth_preflight import check_auth_ready as _check_auth_ready  # noqa: E402
from tools.paths import ensure_project_dirs, pause_flag_path  # noqa: E402

# Pipeline: intake first, then the five consultants. These imports are NOT optional
#, a swallowed ImportError here silently drops an agent, and the run still reports
# success while producing a PRD with that agent's whole question range left blank.
PIPELINE: list[AgentConfig] = [INTAKE, DISCOVERY, DESIGN, DEVELOP, DEPLOY, PM]


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
        return True, "q1-q7 already filled, intake will skip"
    if has_intake_text:
        return True, "intake_text present, intake agent will populate q1-q7"
    return False, "no intake_text and q1-q7 not filled, paste context in the UI first"


def _unanswered(agent: AgentConfig, project_id: str) -> list[str]:
    """Question ids the agent owns that have no answer at all.

    `needs-review` does NOT count. The prompts explicitly instruct agents to use
    it when they've made an assumption a human should check, so it is a correct
    terminal state with real content behind it, treating it as a failure would
    cry wolf on a good run and make resume re-do work that was already done.
    """
    project = qio.load_project(project_id)
    return [
        qid
        for qid in agent.owned_question_ids
        if not qio.get_response(project, qid)[0].strip()
    ]


def _flagged(agent: AgentConfig, project_id: str) -> list[str]:
    """Answered, but the agent flagged an assumption worth a human look."""
    project = qio.load_project(project_id)
    return [
        qid
        for qid in agent.owned_question_ids
        if qio.get_response(project, qid)[1] == "needs-review"
        and qio.get_response(project, qid)[0].strip()
    ]


def _agent_done(agent: AgentConfig, project_id: str) -> bool:
    """An agent is 'done' if every question it owns has a non-empty answer."""
    if not agent.owned_question_ids:
        return False  # PM has no scoped questions; never auto-skip
    return not _unanswered(agent, project_id)


# Transient provider errors (529 overloaded, 5xx, socket resets) otherwise kill a
# ~50-minute pipeline outright. State is written per-question, so a retry resumes
# against work already on disk rather than starting the agent from scratch.
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 5.0


async def _run_agent_with_retry(agent: AgentConfig, project_id: str) -> None:
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            await run_agent(agent, project_id)
            return
        except PipelinePaused:
            raise
        except Exception as e:
            if attempt == RETRY_ATTEMPTS:
                raise
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            runs_log.log_event(
                project_id, agent.name, "retry", attempt=attempt, error=str(e),
            )
            print(f"\n  {agent.name} failed (attempt {attempt}/{RETRY_ATTEMPTS}): {e}")
            print(f"  retrying in {delay:.0f}s…")
            await asyncio.sleep(delay)


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
                print(f"\n[skip] {agent.name}, work already complete (resume mode).")
                continue
            try:
                await _run_agent_with_retry(agent, project_id)
            except PipelinePaused:
                raise
            except Exception as e:
                runs_log.log_event(project_id, agent.name, "error", error=str(e))
                print(f"\nERROR in {agent.name}: {e}")
                return 1
            # Post-condition. An agent that exhausts max_turns still returns cleanly,
            # so without this the pipeline advances with questions silently blank.
            missing = _unanswered(agent, project_id)
            if missing:
                runs_log.log_event(project_id, agent.name, "incomplete", missing=missing)
                print(
                    f"\n[warn] {agent.name} finished with {len(missing)} question(s) "
                    f"unanswered: {', '.join(missing)}\n"
                    f"       Re-run it with `python orchestrator.py only {agent.name} "
                    f"{project_id}` before trusting the final PRD."
                )
            flagged = _flagged(agent, project_id)
            if flagged:
                print(
                    f"  {agent.name}: {len(flagged)} answer(s) marked needs-review "
                    f"({', '.join(flagged)}), answered, but worth your eyes."
                )
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
    # total_cost_usd is None on subscription auth (usage counts against the plan, not
    # a bill) and a real charge on metered credentials, so don't call it free.
    cost_note = "not billed per-token on a subscription" if total_cost == 0 else "billed to your provider"
    print(f"  - API cost: ${total_cost:.2f}  ({cost_note})")
    print(f"  - Token usage: {total_in:,} input / {total_out:,} output")
    return 0


async def cmd_reflect(project_id: str) -> int:
    for agent in PIPELINE:
        if not _agent_done(agent, project_id) and agent.owned_question_ids:
            print(f"  [skip] {agent.name}, its questions aren't all complete yet.")
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
    parser = argparse.ArgumentParser(description="EXCALIBUR, six-agent pipeline orchestrator")
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
