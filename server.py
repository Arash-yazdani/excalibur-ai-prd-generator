#!/usr/bin/env python3
"""AI PM EXCALIBUR — unified FastAPI server.

One process, one port (default :4500), three responsibilities:
  1. Project CRUD (replaces the legacy form tool's stdlib server)
  2. Run dashboard (replaces the dashboard.py from the agentic processor)
  3. Pipeline kickoff with SSE-streamed logs + pause/resume/cancel

The browser hits a single SPA at `/` (the chat UI), with `/legacy-form`
as a fallback for direct question editing when needed.

Run:
  python server.py            # http://localhost:4500
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, AsyncIterator

import markdown as md_renderer
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

load_dotenv(Path(__file__).parent / ".env", override=True)
# Force OAuth (Claude Max) over API key, and strip Claude Desktop's host-IPC
# auth vars. See orchestrator.py for the full rationale; the short version:
#   - ANTHROPIC_API_KEY (even empty) makes the CLI prefer API-key auth
#   - CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST + friends make it expect IPC auth
#     from a host that isn't listening when we're spawned standalone → 401
# Stripping them here AND in orchestrator.py is belt-and-suspenders: the
# server's env is inherited by orchestrator subprocesses, but orchestrator
# also strips defensively in case it's launched from a different shell.
# Prerequisite: user has run `claude setup-token` (one-time persistent OAuth).
for _var in (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDECODE",
):
    os.environ.pop(_var, None)

from framework import load_framework  # noqa: E402
from tools import artifacts as art_tools  # noqa: E402
from tools import handoff as handoff_tools  # noqa: E402
from tools import question_io as qio  # noqa: E402
from tools import runs as runs_log  # noqa: E402
from tools.auth_preflight import check_auth_ready  # noqa: E402
from tools.paths import (  # noqa: E402
    ARTIFACTS_DIR,
    BASE_DIR,
    HANDOFFS_DIR,
    PROJECTS_DIR,
    RUNS_DIR,
    STATIC_DIR,
    ensure_project_dirs,
    lock_path,
    pause_flag_path,
    project_artifacts_dir,
    project_handoffs_dir,
    project_path,
    project_runs_dir,
)

PORT = int(os.environ.get("PORT", 4500))

PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
RUNS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="AI PM EXCALIBUR", version="0.2.0")

# Permissive CORS for localhost dev (the SPA runs on the same origin so this
# is mostly belt-and-suspenders for tools that hit the API directly).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _safe_id(pid: str) -> bool:
    return bool(SAFE_ID_RE.match(pid)) and len(pid) < 100


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]", "-", name).lower()
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "untitled"


def _calc_stats(project: dict[str, Any], framework: dict[str, Any]) -> dict[str, Any]:
    total = sum(len(s["questions"]) for s in framework["sections"])
    data = project.get("data") or {}
    completed = sum(1 for v in data.values() if isinstance(v, dict) and v.get("status") == "complete")
    answered = sum(
        1 for v in data.values() if isinstance(v, dict) and (v.get("response") or "").strip()
    )
    return {
        "total": total,
        "completed": completed,
        "answered": answered,
        "pct": round(completed / total * 100) if total else 0,
    }


def _project_summary(pid: str, framework: dict[str, Any]) -> dict[str, Any]:
    data = json.loads(project_path(pid).read_text())
    return {
        "id": pid,
        "meta": data.get("meta", {}),
        "stats": _calc_stats(data, framework),
        "modified": project_path(pid).stat().st_mtime,
    }


def _clear_run_lock(lock: Path) -> None:
    try:
        lock.unlink()
    except OSError:
        pass


def _is_run_active(pid: str) -> bool:
    """Best-effort check: PID file exists and points at a live orchestrator.

    Reaps our own exited children via waitpid(WNOHANG) so zombie PIDs left after
    a failed run do not block new pipeline starts (macOS kill(0) still succeeds
    on zombies).
    """
    lock = lock_path(pid)
    if not lock.exists():
        return False
    try:
        text = lock.read_text().strip()
        spid = int(text.split("\n", 1)[0]) if text else 0
    except (ValueError, OSError):
        return False
    if spid <= 0:
        _clear_run_lock(lock)
        return False

    try:
        waited_pid, _status = os.waitpid(spid, os.WNOHANG)
        if waited_pid == spid:
            _clear_run_lock(lock)
            return False
    except ChildProcessError:
        pass

    try:
        proc = subprocess.run(
            ["ps", "-p", str(spid), "-o", "state="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            _clear_run_lock(lock)
            return False
        if "Z" in proc.stdout:
            _clear_run_lock(lock)
            return False
    except (OSError, subprocess.TimeoutExpired):
        pass

    try:
        os.kill(spid, 0)
        return True
    except ProcessLookupError:
        _clear_run_lock(lock)
        return False
    except PermissionError:
        return True


def _latest_run_log(pid: str) -> Path | None:
    rd = project_runs_dir(pid)
    if not rd.exists():
        return None
    logs = sorted(rd.glob("run-*.log"), reverse=True)
    return logs[0] if logs else None


def _run_status(pid: str) -> dict[str, Any]:
    """Compose a status object the UI uses to drive its state machine."""
    events = runs_log.read_events(pid)
    # Walk events backward to find the most recent state-defining one.
    state = "idle"
    last_agent = None
    last_ts = None
    last_error: str | None = None
    for e in reversed(events):
        ev = e.get("event", "")
        if ev == "pipeline_complete":
            state = "complete"; last_ts = e.get("ts"); break
        if ev == "pipeline_paused":
            state = "paused"; last_ts = e.get("ts"); break
        if ev == "pipeline_start" or ev == "pipeline_resumed" or ev == "agent_start":
            state = "running"; last_agent = e.get("agent"); last_ts = e.get("ts"); break
        if ev == "error":
            state = "errored"
            last_ts = e.get("ts")
            last_error = e.get("error") or e.get("reason")
            break
        if ev == "auth_failed":
            state = "errored"
            last_ts = e.get("ts")
            last_error = e.get("reason")
            break
    active = _is_run_active(pid)
    if state == "running" and not active:
        # The process died without writing pipeline_complete or pipeline_paused.
        state = "stale"
    return {
        "state": state,
        "last_agent": last_agent,
        "last_ts": last_ts,
        "last_error": last_error,
        "active_process": active,
        "pause_flag": pause_flag_path(pid).exists(),
    }


# ----------------------------------------------------------------------------
# Static + root routes
# ----------------------------------------------------------------------------

# Mount static files — served at /static/*
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root() -> FileResponse:
    """The chat-style SPA."""
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return HTMLResponse(
            "<h1>AI PM EXCALIBUR</h1><p>SPA not built yet — see /docs for the API.</p>",
            status_code=503,
        )
    return FileResponse(index)


@app.get("/legacy-form", response_class=HTMLResponse)
async def legacy_form() -> FileResponse:
    """Fallback: the original form tool form for direct question-by-question editing.
    Use this when the UI doesn't surface the affordance you need
    (e.g. editing one specific answer in isolation)."""
    legacy = STATIC_DIR / "legacy-form.html"
    if not legacy.exists():
        raise HTTPException(404, "Legacy form not preserved")
    return FileResponse(legacy)


# ----------------------------------------------------------------------------
# Framework
# ----------------------------------------------------------------------------

@app.get("/api/framework")
async def get_framework() -> dict[str, Any]:
    """Single source of truth for the 4D phases/sections/questions registry."""
    return load_framework()


# ----------------------------------------------------------------------------
# Project CRUD
# ----------------------------------------------------------------------------

@app.get("/api/projects")
async def list_projects() -> list[dict[str, Any]]:
    fw = load_framework()
    out: list[dict[str, Any]] = []
    for f in sorted(PROJECTS_DIR.glob("*.json")):
        try:
            out.append(_project_summary(f.stem, fw))
        except (json.JSONDecodeError, OSError):
            continue
    out.sort(key=lambda x: x["modified"], reverse=True)
    return out


@app.get("/api/projects/{pid}")
async def get_project(pid: str) -> dict[str, Any]:
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")
    p = project_path(pid)
    if not p.exists():
        raise HTTPException(404, f"Project not found: {pid}")
    return json.loads(p.read_text())


@app.post("/api/projects", status_code=201)
async def create_project(body: dict[str, Any]) -> dict[str, Any]:
    name = (body.get("name") or "untitled").strip() or "untitled"
    pid = _slugify(name)
    base_pid = pid
    counter = 1
    while project_path(pid).exists():
        pid = f"{base_pid}-{counter}"
        counter += 1
    project = {
        "version": 1,
        "meta": body.get("meta") or {
            "name": name,
            "owner": "",
            "industry": "",
            "date": "",
            "intake_text": "",
        },
        "data": {},
    }
    project_path(pid).write_text(json.dumps(project, indent=2))
    ensure_project_dirs(pid)
    return {"id": pid, "project": project}


@app.put("/api/projects/{pid}")
async def save_project(pid: str, body: dict[str, Any]) -> dict[str, Any]:
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")
    if _is_run_active(pid):
        raise HTTPException(
            409,
            "Pipeline running — project is read-only. Pause/cancel the run first.",
        )
    project = {
        "version": 1,
        "meta": body.get("meta") or {},
        "data": body.get("data") or {},
    }
    project_path(pid).write_text(json.dumps(project, indent=2))
    return {"ok": True, "id": pid}


@app.patch("/api/projects/{pid}")
async def patch_project(pid: str, body: dict[str, Any]) -> dict[str, Any]:
    """Partial update. Used by the UI to update meta + intake_text
    without sending the full project body. Body shape:
      {"meta": {"name": "...", "intake_text": "..."}}  — any subset"""
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")
    if _is_run_active(pid):
        raise HTTPException(409, "Pipeline running — project is read-only.")
    p = project_path(pid)
    if not p.exists():
        raise HTTPException(404, f"Project not found: {pid}")
    project = json.loads(p.read_text())
    if "meta" in body:
        project.setdefault("meta", {}).update(body["meta"] or {})
    if "data" in body:
        project.setdefault("data", {}).update(body["data"] or {})
    p.write_text(json.dumps(project, indent=2))
    return {"ok": True, "id": pid}


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str) -> dict[str, Any]:
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")
    if _is_run_active(pid):
        raise HTTPException(409, "Cannot delete: pipeline running.")
    p = project_path(pid)
    if p.exists():
        p.unlink()
    return {"ok": True}


# ----------------------------------------------------------------------------
# Pipeline runs (start, pause, resume, cancel, stream, status)
# ----------------------------------------------------------------------------

def _spawn_orchestrator(pid: str, *, resume: bool = False) -> Path:
    """Start the orchestrator as a detached subprocess. Writes its PID + log path
    to the per-project lock file. Returns the log file path."""
    rd = project_runs_dir(pid)
    rd.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = rd / f"run-{ts}.log"
    cmd = [
        sys.executable,
        "-u",  # unbuffered stdout/stderr so the SSE log tail sees lines as
               # they're printed, not in 4KB block-flush bursts. Without this,
               # the running view appears stuck for ~90s while early-pipeline
               # output accumulates in the orchestrator's stdio buffer.
        str(BASE_DIR / "orchestrator.py"),
        "resume" if resume else "run",
        pid,
    ]
    log_fh = log_path.open("ab", buffering=0)
    proc = subprocess.Popen(  # noqa: S603 — we control all args
        cmd,
        cwd=str(BASE_DIR),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        # Detach from this parent so the run survives a server restart.
        start_new_session=True,
    )
    lock_path(pid).write_text(f"{proc.pid}\n{log_path}\n")
    return log_path


@app.post("/api/projects/{pid}/run")
async def start_run(pid: str) -> dict[str, Any]:
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")
    if not project_path(pid).exists():
        raise HTTPException(404, f"Project not found: {pid}")
    auth_ok, auth_reason = check_auth_ready(probe_api_call=True)
    if not auth_ok:
        runs_log.log_event(pid, "orchestrator", "auth_failed", reason=auth_reason)
        raise HTTPException(
            503,
            f"Claude Max OAuth not ready: {auth_reason}",
        )
    if _is_run_active(pid):
        raise HTTPException(409, "A run is already active for this project.")
    # Clear any stale pause flag from a prior run.
    pf = pause_flag_path(pid)
    if pf.exists():
        pf.unlink()
    log_path = _spawn_orchestrator(pid, resume=False)
    return {"ok": True, "log": str(log_path), "status": _run_status(pid)}


@app.post("/api/projects/{pid}/resume")
async def resume_run(pid: str) -> dict[str, Any]:
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")
    if not project_path(pid).exists():
        raise HTTPException(404, f"Project not found: {pid}")
    auth_ok, auth_reason = check_auth_ready(probe_api_call=True)
    if not auth_ok:
        runs_log.log_event(pid, "orchestrator", "auth_failed", reason=auth_reason)
        raise HTTPException(503, f"Claude Max OAuth not ready: {auth_reason}")
    if _is_run_active(pid):
        raise HTTPException(409, "A run is already active for this project.")
    log_path = _spawn_orchestrator(pid, resume=True)
    return {"ok": True, "log": str(log_path), "status": _run_status(pid)}


@app.post("/api/projects/{pid}/pause")
async def pause_run(pid: str) -> dict[str, Any]:
    """Soft-pause: writes the flag; the orchestrator exits cleanly between agents."""
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")
    ensure_project_dirs(pid)
    pf = pause_flag_path(pid)
    pf.write_text("paused\n")
    runs_log.log_event(pid, "orchestrator", "pause_requested_via_ui")
    return {"ok": True, "flag": str(pf)}


@app.post("/api/projects/{pid}/cancel")
async def cancel_run(pid: str) -> dict[str, Any]:
    """Hard cancel: SIGTERM the subprocess. In-flight question writes may be lost."""
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")
    lock = lock_path(pid)
    if not lock.exists():
        return {"ok": True, "note": "no active run"}
    try:
        spid = int(lock.read_text().split("\n", 1)[0])
    except (ValueError, OSError):
        return {"ok": True, "note": "lock unreadable; cleared"}
    try:
        os.killpg(spid, signal.SIGTERM)  # process group, since we used start_new_session
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(spid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    runs_log.log_event(pid, "orchestrator", "pipeline_cancelled_via_ui", pid_killed=spid)
    try:
        lock.unlink()
    except OSError:
        pass
    return {"ok": True, "killed": spid}


@app.get("/api/projects/{pid}/status")
async def project_status(pid: str) -> dict[str, Any]:
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")
    if not project_path(pid).exists():
        raise HTTPException(404, f"Project not found: {pid}")
    return _run_status(pid)


@app.get("/api/projects/{pid}/stream")
async def stream_run(pid: str, request: Request) -> EventSourceResponse:
    """SSE stream: tails the latest run log + emits a final status event when done."""
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")

    log_path = _latest_run_log(pid)
    if log_path is None:
        raise HTTPException(404, "No runs yet for this project")

    async def gen() -> AsyncIterator[dict[str, Any]]:
        # Open and read all existing content first so reconnects get full state.
        with log_path.open("rb") as f:
            buf = b""
            # Initial replay of the existing log.
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if await request.is_disconnected():
                        return
                    yield {"event": "log", "data": line.decode("utf-8", errors="replace")}
            # Tail mode: emit new lines as they arrive, with periodic heartbeats.
            ticks_since_data = 0
            while True:
                if await request.is_disconnected():
                    return
                chunk = f.read(4096)
                if chunk:
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        yield {"event": "log", "data": line.decode("utf-8", errors="replace")}
                    ticks_since_data = 0
                else:
                    ticks_since_data += 1
                    # Check if the run has terminated. If yes, emit a final
                    # status event and close the stream.
                    if not _is_run_active(pid):
                        # Drain any trailing partial line.
                        if buf:
                            yield {"event": "log", "data": buf.decode("utf-8", errors="replace")}
                            buf = b""
                        yield {"event": "status", "data": json.dumps(_run_status(pid))}
                        return
                    # Heartbeat every ~10s when idle to keep the connection alive.
                    if ticks_since_data % 20 == 0:
                        yield {"event": "heartbeat", "data": _dt.datetime.now(_dt.timezone.utc).isoformat()}
                    await asyncio.sleep(0.5)

    return EventSourceResponse(gen())


# ----------------------------------------------------------------------------
# Run history (timeline, handoffs, artifacts)
# ----------------------------------------------------------------------------

@app.get("/api/runs")
async def list_runs() -> list[dict[str, Any]]:
    """Return one entry per project that has any run history."""
    out: list[dict[str, Any]] = []
    for pid in runs_log.all_project_ids():
        try:
            project = qio.load_project(pid)
        except FileNotFoundError:
            continue
        events = runs_log.read_events(pid)
        completes = [e for e in events if e.get("event") == "agent_complete"]
        cost = sum(float(e.get("total_cost_usd") or 0.0) for e in completes)
        out.append({
            "id": pid,
            "name": project.get("meta", {}).get("name", pid),
            "status": _run_status(pid),
            "events_count": len(events),
            "agents_complete": len(completes),
            "equiv_cost_usd": round(cost, 2),
            "first_event_ts": events[0].get("ts") if events else None,
            "last_event_ts": events[-1].get("ts") if events else None,
        })
    return out


@app.get("/api/projects/{pid}/timeline")
async def project_timeline(pid: str) -> list[dict[str, Any]]:
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")
    return runs_log.read_events(pid)


@app.get("/api/projects/{pid}/handoffs")
async def list_handoffs(pid: str) -> list[dict[str, Any]]:
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")
    d = project_handoffs_dir(pid)
    if not d.exists():
        return []
    return [{"name": p.stem, "size": p.stat().st_size} for p in sorted(d.glob("*.md"))]


@app.get("/api/projects/{pid}/handoffs/{name}")
async def get_handoff(pid: str, name: str) -> dict[str, Any]:
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")
    p = project_handoffs_dir(pid) / f"{name}.md"
    if not p.exists():
        raise HTTPException(404, f"Handoff not found: {name}")
    raw = p.read_text()
    return {"name": name, "raw": raw, "html": md_renderer.markdown(raw, extensions=["fenced_code", "tables"])}


@app.get("/api/projects/{pid}/artifacts")
async def list_artifacts(pid: str) -> list[dict[str, Any]]:
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")
    d = project_artifacts_dir(pid)
    if not d.exists():
        return []
    return [{"name": p.stem, "size": p.stat().st_size} for p in sorted(d.glob("*.md"))]


@app.get("/api/projects/{pid}/artifacts/{name}")
async def get_artifact(pid: str, name: str) -> dict[str, Any]:
    if not _safe_id(pid):
        raise HTTPException(400, "Invalid project id")
    p = project_artifacts_dir(pid) / f"{name}.md"
    if not p.exists():
        raise HTTPException(404, f"Artifact not found: {name}")
    raw = p.read_text()
    return {"name": name, "raw": raw, "html": md_renderer.markdown(raw, extensions=["fenced_code", "tables"])}


# ----------------------------------------------------------------------------
# Entry
# ----------------------------------------------------------------------------

def main() -> None:
    import uvicorn
    print(f"\n  AI PM EXCALIBUR running on http://localhost:{PORT}\n")
    print(f"  chat UI:  http://localhost:{PORT}/")
    print(f"  Legacy form view:    http://localhost:{PORT}/legacy-form")
    print(f"  API docs:            http://localhost:{PORT}/docs\n")
    print(f"  Projects dir:        {PROJECTS_DIR}")
    print(f"  Press Ctrl-C to stop\n")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
