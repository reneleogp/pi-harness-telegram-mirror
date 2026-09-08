#!/usr/bin/env python3
"""Bounded, read-only Firstmate worker status integration for /workers."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import signal
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

COMMAND_OUTPUT_LIMIT = 512 * 1024
METADATA_LIMIT = 64 * 1024
FLEET_COMMAND_TIMEOUT = 12
HERDR_COMMAND_TIMEOUT = 3
WORKER_MESSAGE_LIMIT = 3900
FIELD_LIMIT = 180
SUPPORTED_HERDR_STATUSES = {"working", "idle", "blocked", "done", "unknown"}
SUPPORTED_BACKENDS = {"tmux", "herdr", "zellij", "orca", "cmux"}
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ENDPOINT_ATOM_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@%+:-]*$")
STATE_LINE_PATTERN = re.compile(
    r"^state: ([a-z-]+) · source: ([a-z-]+)(?: · (.*))?$"
)
PRIVATE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:~[/\\]|/(?:Users|home|private|tmp)/|[A-Za-z]:\\)[^\s,;]*"
)
SECRET_PATTERN = re.compile(
    r"(?i)\b(token|password|secret|api[_-]?key)\s*[=:]\s*[^\s,;]+"
)


class WorkersUnavailable(RuntimeError):
    """The connected session cannot provide a safe Firstmate worker view."""


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    returncode: int
    timed_out: bool = False
    overflow: bool = False


@dataclass(frozen=True)
class ManagedWorker:
    name: str
    backend: str
    target_session: Optional[str]
    target_pane: Optional[str]
    remote: bool
    metadata_valid: bool
    metadata_path: Path
    metadata_digest: str


@dataclass(frozen=True)
class WorkerView:
    name: str
    description: str
    herdr_status: str
    herdr_note: str
    progress: str
    progress_note: str


def _capture_readonly(argv: list[str], *, timeout: int,
                      env: Optional[dict[str, str]] = None) -> CommandResult:
    """Capture one of this module's fixed read-only commands with hard bounds."""
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
    except OSError:
        return CommandResult("", 127)

    captured = bytearray()
    overflow = False

    def drain() -> None:
        nonlocal overflow
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                return
            remaining = COMMAND_OUTPUT_LIMIT + 1 - len(captured)
            if remaining > 0:
                captured.extend(chunk[:remaining])
            if len(captured) > COMMAND_OUTPUT_LIMIT or len(chunk) > remaining:
                overflow = True

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    reader.join(timeout=1)
    if reader.is_alive():
        overflow = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        reader.join(timeout=1)
    output = bytes(captured[:COMMAND_OUTPUT_LIMIT]).decode("utf-8", "replace")
    return CommandResult(output, process.returncode if process.returncode is not None else -1,
                         timed_out=timed_out, overflow=overflow)


def _regular_owned_file(path: Path) -> bool:
    try:
        stat = path.lstat()
    except OSError:
        return False
    uid = getattr(os, "getuid", None)
    return (
        path.is_file()
        and not path.is_symlink()
        and (uid is None or stat.st_uid == uid())
        and not stat.st_mode & 0o022
    )


def _firstmate_paths(home: Path) -> tuple[Path, Path, Path]:
    try:
        canonical = home.resolve(strict=True)
    except OSError as exc:
        raise WorkersUnavailable("connected Firstmate home is unavailable") from exc
    state = canonical / "state"
    bin_dir = canonical / "bin"
    data_dir = canonical / "data"
    crew_state = bin_dir / "fm-crew-state.sh"
    backlog = data_dir / "backlog.md"
    if (
        not state.is_dir() or state.is_symlink()
        or not bin_dir.is_dir() or bin_dir.is_symlink()
        or not data_dir.is_dir() or data_dir.is_symlink()
        or not _regular_owned_file(crew_state)
    ):
        raise WorkersUnavailable("connected session is not a supported Firstmate home")
    return state, crew_state, backlog


def _read_metadata(path: Path) -> Optional[tuple[dict[str, list[str]], str]]:
    try:
        stat = path.lstat()
        if (not _regular_owned_file(path) or stat.st_size > METADATA_LIMIT):
            return None
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError):
        return None
    values: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values.setdefault(key, []).append(value)
    return values, hashlib.sha256(raw).hexdigest()


def _exact(values: dict[str, list[str]], key: str) -> Optional[str]:
    found = values.get(key, [])
    if len(found) != 1 or not found[0] or any(char in found[0] for char in "\r\n\t"):
        return None
    return found[0]


def _worker_from_meta(path: Path) -> ManagedWorker:
    name = path.name[:-5]
    parsed = _read_metadata(path)
    if parsed is None or not TASK_ID_PATTERN.fullmatch(name):
        return ManagedWorker(
            safe_text(name, 100), "unknown", None, None, False, False,
            path, "",
        )
    values, digest = parsed
    backend_values = values.get("backend", [])
    backend = "tmux" if not backend_values else _exact(values, "backend") or "unknown"
    if backend not in SUPPORTED_BACKENDS:
        backend = "unknown"
    remote_values = values.get("remote_host", [])
    remote_host = _exact(values, "remote_host")
    remote = bool(remote_host)
    remote_valid = not remote_values or remote_host is not None
    if backend != "herdr":
        return ManagedWorker(
            name, backend, None, None, remote, backend != "unknown",
            path, digest,
        )

    binding = _exact(values, "endpoint_task_id")
    session = _exact(values, "herdr_session")
    pane = _exact(values, "herdr_pane_id")
    window = _exact(values, "window")
    workspace = _exact(values, "herdr_workspace_id")
    tab = _exact(values, "herdr_tab_id")
    worktree = _exact(values, "worktree")
    project = _exact(values, "project")
    valid = bool(
        remote_valid
        and binding == name
        and session and pane and window == f"{session}:{pane}"
        and workspace and tab and worktree and project
        and ENDPOINT_ATOM_PATTERN.fullmatch(session)
        and ENDPOINT_ATOM_PATTERN.fullmatch(pane)
        and ENDPOINT_ATOM_PATTERN.fullmatch(workspace)
        and ENDPOINT_ATOM_PATTERN.fullmatch(tab)
    )
    return ManagedWorker(
        name, backend,
        session if valid else None,
        pane if valid else None,
        remote,
        valid,
        path,
        digest,
    )


def _metadata_is_current(worker: ManagedWorker) -> bool:
    parsed = _read_metadata(worker.metadata_path)
    return bool(parsed is not None and worker.metadata_digest
                and parsed[1] == worker.metadata_digest)


def _managed_workers(state: Path) -> list[ManagedWorker]:
    try:
        paths = sorted(state.glob("*.meta"), key=lambda item: item.name)
    except OSError as exc:
        raise WorkersUnavailable("Firstmate worker records are unavailable") from exc
    workers: list[ManagedWorker] = []
    for path in paths:
        if not _regular_owned_file(path) or _read_metadata(path) is None:
            continue
        workers.append(_worker_from_meta(path))
    return workers


def _task_titles(backlog: Path) -> Optional[dict[str, str]]:
    if not _regular_owned_file(backlog):
        return None
    result = _capture_readonly(
        ["tasks-axi", "list", "--file", str(backlog)],
        timeout=FLEET_COMMAND_TIMEOUT,
    )
    if result.returncode != 0 or result.timed_out or result.overflow:
        return None
    titles: dict[str, str] = {}
    in_rows = False
    saw_rows_header = False
    for line in result.stdout.splitlines():
        if re.match(r"^tasks\[\d+\]\{id,state,kind,repo,title\}:$", line):
            in_rows = True
            saw_rows_header = True
            continue
        if not in_rows:
            continue
        if not line.startswith("  "):
            break
        try:
            row = next(csv.reader([line.strip()]))
        except (csv.Error, StopIteration):
            continue
        if len(row) == 5 and TASK_ID_PATTERN.fullmatch(row[0]):
            title = safe_text(row[4], FIELD_LIMIT)
            titles[row[0]] = title or "Description unavailable"
    return titles if saw_rows_header else None


def _progress(home: Path, helper: Path, worker: ManagedWorker) -> tuple[str, str]:
    # Do not let ambient Firstmate overrides redirect this fixed helper away
    # from the connected home selected by the authorized Pi peer.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("FM_")}
    env.update({
        "FM_HOME": str(home),
        "FM_ROOT_OVERRIDE": str(home),
        "FM_STATE_OVERRIDE": str(home / "state"),
        "FM_DATA_OVERRIDE": str(home / "data"),
        "FM_PROJECTS_OVERRIDE": str(home / "projects"),
        "FM_CONFIG_OVERRIDE": str(home / "config"),
    })
    result = _capture_readonly(
        [str(helper), worker.name], timeout=FLEET_COMMAND_TIMEOUT, env=env,
    )
    if result.timed_out:
        return "unknown", "current-state check timed out"
    if result.returncode != 0 or result.overflow:
        return "unknown", "current-state check unavailable"
    line = result.stdout.splitlines()[0] if result.stdout.splitlines() else ""
    match = STATE_LINE_PATTERN.fullmatch(line)
    if match is None:
        return "unknown", "current-state response malformed"
    state, _source, detail = match.groups()
    supported = {"working", "parked", "done", "blocked", "paused", "failed", "unknown"}
    return (state if state in supported else "unknown", safe_text(detail or "", FIELD_LIMIT))


def _herdr_status(worker: ManagedWorker) -> tuple[str, str]:
    if worker.backend != "herdr":
        return "unknown", "Not running in Herdr"
    if worker.remote:
        return "unknown", "remote Herdr status unavailable"
    if not worker.metadata_valid or not worker.target_session or not worker.target_pane:
        return "unknown", "endpoint metadata unavailable"
    env = dict(os.environ)
    env["HERDR_SESSION"] = worker.target_session
    result = _capture_readonly(
        ["herdr", "agent", "get", worker.target_pane,
         "--session", worker.target_session],
        timeout=HERDR_COMMAND_TIMEOUT, env=env,
    )
    if result.timed_out:
        return "unknown", "Herdr status timed out"
    if result.overflow:
        return "unknown", "Herdr status response too large"
    if result.returncode != 0:
        return "unknown", "Herdr status unavailable"
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return "unknown", "Herdr status unavailable"
    if not isinstance(payload, dict):
        return "unknown", "Herdr status unavailable"
    result_payload = payload.get("result")
    if isinstance(result_payload, dict):
        agent = result_payload.get("agent")
        if isinstance(agent, dict):
            for identity_key in ("task_id", "endpoint_task_id"):
                identity = agent.get(identity_key)
                if identity is not None:
                    if not isinstance(identity, str):
                        return "unknown", "Herdr status unavailable"
                    if identity != worker.name:
                        return "unknown", "Herdr endpoint absent"
            status = agent.get("agent_status")
            if isinstance(status, str) and status in SUPPORTED_HERDR_STATUSES:
                return status, ""
        else:
            return "unknown", "Herdr status unavailable"
    error = payload.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if isinstance(code, str) and code in {"agent_not_found", "pane_not_found"}:
        return "unknown", "Herdr endpoint absent"
    return "unknown", "Herdr status unavailable"


def safe_text(value: str, limit: int) -> str:
    text = " ".join(str(value).replace("\x00", "").split())
    text = PRIVATE_PATH_PATTERN.sub("[private path]", text)
    text = SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    if len(text) > limit:
        return text[:max(0, limit - 1)].rstrip() + "…"
    return text


def collect_worker_views(home: Path) -> list[WorkerView]:
    state, helper, backlog = _firstmate_paths(home)
    canonical = state.parent
    workers = _managed_workers(state)
    titles = _task_titles(backlog)
    if titles is None:
        raise WorkersUnavailable("current worker task records are unavailable")
    workers = [worker for worker in workers if worker.name in titles]

    def inspect(worker: ManagedWorker) -> WorkerView:
        description = titles.get(worker.name, "Description unavailable")
        if not _metadata_is_current(worker):
            return WorkerView(
                worker.name, description, "unknown",
                "task record unavailable", "unknown", "task record unavailable",
            )
        progress, progress_note = _progress(canonical, helper, worker)
        herdr_status, herdr_note = _herdr_status(worker)
        if not _metadata_is_current(worker):
            return WorkerView(
                worker.name, description, "unknown",
                "task record changed during status read", "unknown",
                "task record changed during status read",
            )
        return WorkerView(
            worker.name,
            description,
            herdr_status,
            herdr_note,
            progress,
            progress_note,
        )

    if not workers:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(workers))) as pool:
        views = list(pool.map(inspect, workers))
    return [view for view in views
            if view.herdr_note != "Herdr endpoint absent"]


def _entry(view: WorkerView) -> str:
    icon = {
        "working": "🟢",
        "idle": "⚪",
        "blocked": "⛔",
        "done": "✅",
    }.get(view.herdr_status, "❔")
    lines = [
        f"{icon} {safe_text(view.name, 100)} - {safe_text(view.description, FIELD_LIMIT)}",
        f"Herdr: {view.herdr_status}",
    ]
    if view.herdr_note:
        lines[-1] += f" ({safe_text(view.herdr_note, FIELD_LIMIT)})"
    progress = f"Progress: {view.progress}"
    if view.progress_note:
        progress += f" - {safe_text(view.progress_note, FIELD_LIMIT)}"
    lines.append(progress)
    return "\n".join(lines)


def worker_messages(home: Path, limit: int = WORKER_MESSAGE_LIMIT) -> list[str]:
    views = collect_worker_views(home)
    if not views:
        return ["No Firstmate-managed workers."]
    entries = [_entry(view) for view in views]
    messages: list[str] = []
    current = "Firstmate workers"
    for entry in entries:
        candidate = f"{current}\n\n{entry}" if current else entry
        if len(candidate.encode("utf-16-le")) // 2 <= limit:
            current = candidate
            continue
        if current:
            messages.append(current)
        current = entry
    if current:
        messages.append(current)
    return messages
