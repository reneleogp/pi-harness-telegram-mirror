from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import socket
import sys
import threading

import pytest
from pathlib import Path

ROOT = Path(__file__).parents[1]
BOT = ROOT / "bin/pi-telegram.py"
OWNER = ROOT / "bin/pi-telegram-owner.py"
WORKERS = ROOT / "bin/pi_telegram_workers.py"


def load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def firstmate_home(tmp_path):
    home = tmp_path / "firstmate"
    (home / "state").mkdir(parents=True)
    (home / "bin").mkdir()
    (home / "data").mkdir()
    (home / "projects").mkdir()
    (home / "config").mkdir()
    helper = home / "bin/fm-crew-state.sh"
    helper.write_text("#!/bin/sh\nexit 0\n")
    helper.chmod(0o755)
    backlog = home / "data/backlog.md"
    backlog.write_text("# Tasks\n")
    backlog.chmod(0o644)
    return home


def herdr_meta(name, session, pane, *, kind="ship", remote=False):
    lines = [
        "backend=herdr",
        f"window={session}:{pane}",
        f"endpoint_task_id={name}",
        f"herdr_session={session}",
        "herdr_workspace_id=w1",
        "herdr_tab_id=w1:t1",
        f"herdr_pane_id={pane}",
        f"worktree=/private/work/{name}",
        "project=repo",
        f"kind={kind}",
    ]
    if remote:
        lines.append("remote_host=builder")
    return "\n".join(lines) + "\n"


def fake_runner_for(workers, states, *, current=None):
    calls = []
    current = current or {}

    def run(argv, *, timeout, env=None):
        calls.append(tuple(argv))
        if argv[0] == "tasks-axi":
            rows = "\n".join(
                f"  {name},in_flight,ship,repo,{title}"
                for name, title in workers.items()
            )
            return workers_module.CommandResult(
                f"count: {len(workers)}\n"
                f"tasks[{len(workers)}]{{id,state,kind,repo,title}}:\n{rows}\n"
                "help[1]:\n", 0
            )
        if argv[0] == "herdr":
            pane = argv[3]
            value = states[pane]
            if isinstance(value, workers_module.CommandResult):
                return value
            return workers_module.CommandResult(
                '{"result":{"agent":{"agent_status":"' + value + '"}}}', 0
            )
        name = argv[-1]
        value = current.get(name, "state: working · source: pane · harness busy")
        if isinstance(value, workers_module.CommandResult):
            return value
        return workers_module.CommandResult(value + "\n", 0)

    return run, calls


workers_module = load_path("pi_telegram_workers_tests", WORKERS)


def live_snapshot():
    return {
        "version": "0.8.2",
        "protocol": 20,
        "workspaces": [
            {"workspace_id": "w1", "label": "firstmate", "agent_status": "working"},
            {"workspace_id": "w2", "label": "managed", "agent_status": "idle"},
            {"workspace_id": "w3", "label": "shell", "agent_status": "unknown"},
        ],
        "agents": [
            {"workspace_id": "w1", "agent": "pi", "agent_status": "working"},
            {"workspace_id": "w2", "agent": "codex", "agent_status": "idle"},
        ],
    }


def test_live_herdr_workspaces_include_primary_workers_and_shells_without_metadata(
    tmp_path, monkeypatch,
):
    home = firstmate_home(tmp_path)
    # This stale record must not turn the shell workspace into a worker.
    (home / "state/stale.meta").write_text(herdr_meta("stale", "other", "w3:p1"))
    monkeypatch.setattr(workers_module, "_herdr_snapshot", lambda _path: live_snapshot())

    messages = workers_module.worker_messages(
        home, herdr_socket_path=str(tmp_path / "named-herdr.sock"),
        herdr_workspace_id="w1",
    )
    text = "\n".join(messages)
    assert "Firstmate - firstmate" in text
    assert "Worker - managed" in text
    assert "Workspace - shell" in text
    assert "Live agents: none" in text
    assert "stale" not in text
    assert text.index("Firstmate - firstmate") < text.index("Worker - managed") < text.index("Workspace - shell")


def test_live_herdr_snapshot_uses_only_open_workspaces_and_does_not_leak_other_sessions(
    tmp_path, monkeypatch,
):
    home = firstmate_home(tmp_path)
    # Herdr's live session.snapshot contains open workspaces only. A closed
    # workspace is absent from this source and therefore cannot be fabricated.
    snapshot = live_snapshot()
    def snapshot_for(path):
        if path == "named-a.sock":
            return snapshot
        if path == "named-b.sock":
            return {
                **live_snapshot(),
                "workspaces": [{"workspace_id": "other", "label": "other-session", "agent_status": "idle"}],
                "agents": [],
            }
        raise workers_module.WorkersUnavailable("connected Herdr session is unavailable")

    monkeypatch.setattr(workers_module, "_herdr_snapshot", snapshot_for)
    # The exact socket is the session boundary. No ambient session or second
    # named session is consulted.
    text = "\n".join(workers_module.worker_messages(
        home, herdr_socket_path="named-a.sock", herdr_workspace_id="w1"
    ))
    assert "closed" not in text
    assert "other-session" not in text
    with pytest.raises(workers_module.WorkersUnavailable):
        workers_module.worker_messages(
            home, herdr_socket_path="missing.sock",
            herdr_workspace_id="w1",
        )


def test_live_herdr_workspace_pagination_is_bounded_and_ordered(tmp_path, monkeypatch):
    home = firstmate_home(tmp_path)
    workspaces = [
        {"workspace_id": f"w{index}", "label": f"workspace-{index:02d}", "agent_status": "idle"}
        for index in range(45)
    ]
    monkeypatch.setattr(workers_module, "_herdr_snapshot", lambda _path: {
        "workspaces": workspaces, "agents": [],
    })
    messages = workers_module.worker_messages(
        home, limit=700, herdr_socket_path="named.sock", herdr_workspace_id="w999"
    )
    assert len(messages) > 1
    assert all(len(message.encode("utf-16-le")) // 2 <= 700 for message in messages)
    combined = "\n".join(messages)
    for index in range(45):
        assert combined.count(f"Workspace - workspace-{index:02d}") == 1


def test_live_herdr_api_snapshot_protocol_is_bounded_and_exact(tmp_path):
    path = f"/tmp/pi-telegram-workers-{os.getpid()}.sock"
    Path(path).unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    os.chmod(path, 0o600)
    server.listen(1)
    received = []

    def serve():
        connection, _ = server.accept()
        with connection:
            received.append(json.loads(connection.recv(4096).decode()))
            connection.sendall(json.dumps({
                "id": "pi-telegram-workers",
                "result": {"type": "session_snapshot", "snapshot": live_snapshot()},
            }).encode() + b"\n")

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        snapshot = workers_module._herdr_snapshot(path)
    finally:
        thread.join(timeout=2)
        server.close()
        Path(path).unlink(missing_ok=True)
    assert received == [{"id": "pi-telegram-workers", "method": "session.snapshot", "params": {}}]
    assert snapshot["workspaces"][0]["workspace_id"] == "w1"


def test_only_owned_exact_endpoints_are_queried_and_all_statuses_are_preserved(tmp_path, monkeypatch):
    home = firstmate_home(tmp_path)
    titles = {}
    native = {}
    progress = {}
    for index, status in enumerate(("working", "idle", "blocked", "done"), 1):
        name = f"worker-{status}"
        pane = f"w{index}:p2"
        (home / "state" / f"{name}.meta").write_text(herdr_meta(name, "fleet", pane))
        titles[name] = f"Task for {status}"
        native[pane] = status
        progress[name] = (
            "state: working · source: run-step · validation running"
            if status == "idle"
            else f"state: {status} · source: pane · current {status} reason"
        )
    runner, calls = fake_runner_for(titles, native, current=progress)
    monkeypatch.setattr(workers_module, "_capture_readonly", runner)

    views = workers_module.collect_worker_views(home)

    assert [view.name for view in views] == sorted(titles)
    assert {view.herdr_status for view in views} == {"working", "idle", "blocked", "done"}
    idle = next(view for view in views if view.name == "worker-idle")
    assert idle.herdr_status == "idle"
    assert idle.progress == "working"
    assert idle.progress_note == "validation running"
    queried = {call[3] for call in calls if call[0] == "herdr"}
    assert queried == set(native)
    assert "unrelated:p9" not in queried
    for call in (call for call in calls if call[0] == "herdr"):
        assert call[1:3] == ("agent", "get")
        assert call[-2:] == ("--session", "fleet")


def test_live_done_worker_keeps_description_without_showing_unrelated_done_task(tmp_path, monkeypatch):
    home = firstmate_home(tmp_path)
    (home / "state/done-worker.meta").write_text(
        herdr_meta("done-worker", "fleet", "w1:p1")
    )
    (home / "state/retired-worker.meta").write_text(
        herdr_meta("retired-worker", "fleet", "w2:p1")
    )
    (home / "state/empty-worker.meta").write_text(
        herdr_meta("empty-worker", "fleet", "w3:p1")
    )
    calls = []

    def run(argv, *, timeout, env=None):
        calls.append(tuple(argv))
        if argv[0] == "tasks-axi":
            return workers_module.CommandResult(
                "count: 2\n"
                "tasks[3]{id,state,kind,repo,title}:\n"
                "  done-worker,done,ship,repo,Completed task description\n"
                "  retired-done,done,ship,repo,Retired task\n"
                "  empty-worker,done,ship,repo,   \n"
                "help[1]:\n", 0,
            )
        if argv[0] == "herdr":
            return workers_module.CommandResult(
                '{"result":{"agent":{"agent_status":"done"}}}', 0,
            )
        return workers_module.CommandResult(
            "state: done · source: pane · completed\n", 0,
        )

    monkeypatch.setattr(workers_module, "_capture_readonly", run)
    message = workers_module.worker_messages(home)[0]

    assert "done-worker - Completed task description" in message
    assert "empty-worker - Description unavailable" in message
    assert "retired-worker" not in message
    assert "retired-done" not in message
    task_calls = [call for call in calls if call[0] == "tasks-axi"]
    assert task_calls == [("tasks-axi", "list", "--file", str(home / "data/backlog.md"))]


def test_unsafe_state_records_are_not_listed(tmp_path, monkeypatch):
    home = firstmate_home(tmp_path)
    target = tmp_path / "outside.meta"
    target.write_text(herdr_meta("symlinked", "fleet", "w1:p1"))
    (home / "state/symlinked.meta").symlink_to(target)
    (home / "state/directory.meta").mkdir()
    (home / "state/unreadable.meta").write_bytes(b"\xff")
    runner, _ = fake_runner_for(
        {"symlinked": "Symlinked", "directory": "Directory", "unreadable": "Unreadable"},
        {},
    )
    monkeypatch.setattr(workers_module, "_capture_readonly", runner)

    assert workers_module.worker_messages(home) == ["No Firstmate task records."]


def test_empty_fleet_and_non_herdr_task_are_honest(tmp_path, monkeypatch):
    home = firstmate_home(tmp_path)
    runner, _ = fake_runner_for({}, {})
    monkeypatch.setattr(workers_module, "_capture_readonly", runner)
    assert workers_module.worker_messages(home) == ["No Firstmate task records."]

    (home / "state/tmux-worker.meta").write_text(
        "backend=tmux\nwindow=default:fm-tmux-worker\nworktree=/private/work/x\nproject=repo\n"
    )
    runner, calls = fake_runner_for(
        {"tmux-worker": "Uses a different runtime"}, {},
        current={"tmux-worker": "state: paused · source: status-log · awaiting dependency"},
    )
    monkeypatch.setattr(workers_module, "_capture_readonly", runner)
    message = workers_module.worker_messages(home)[0]
    assert message.startswith("Firstmate task records")
    assert "Herdr: unknown (Not running in Herdr)" in message
    assert "Progress: paused - awaiting dependency" in message
    assert not any(call[0] == "herdr" for call in calls)


def test_absent_timeout_malformed_and_remote_endpoints_report_unknown(tmp_path, monkeypatch):
    home = firstmate_home(tmp_path)
    cases = {
        "absent": ("w1:p1", workers_module.CommandResult(
            '{"error":{"code":"agent_not_found"}}', 0)),
        "failed": ("w2:p2", workers_module.CommandResult(
            '{"result":{"agent":{"agent_status":"working"}}}', 1)),
        "timeout": ("w3:p1", workers_module.CommandResult("", -1, timed_out=True)),
        "malformed": ("w4:p1", workers_module.CommandResult("not json", 0)),
        "result-malformed": ("w5:p1", workers_module.CommandResult(
            '{"result":"malformed"}', 0)),
        "agent-malformed": ("w6:p1", workers_module.CommandResult(
            '{"result":{"agent":[]}}', 0)),
        "status-malformed": ("w7:p1", workers_module.CommandResult(
            '{"result":{"agent":{"agent_status":[]}}}', 0)),
        "error-malformed": ("w8:p1", workers_module.CommandResult(
            '{"error":{"code":[]}}', 0)),
        "identity-malformed": ("w9:p1", workers_module.CommandResult(
            '{"result":{"agent":{"task_id":[]}}}', 0)),
        "identity-empty": ("w10:p2", workers_module.CommandResult(
            '{"result":{"agent":{"task_id":""}}}', 0)),
    }
    for name, (pane, _result) in cases.items():
        (home / "state" / f"{name}.meta").write_text(herdr_meta(name, "fleet", pane))
    (home / "state/remote.meta").write_text(
        herdr_meta("remote", "remote-fleet", "w11:p1", kind="secondmate", remote=True)
    )
    (home / "state/bad.meta").write_text(
        herdr_meta("bad", "fleet", "w12:p1") + "endpoint_task_id=someone-else\n"
    )
    results = {pane: result for pane, result in cases.values()}
    runner, calls = fake_runner_for(
        {name: f"Description {name}" for name in [*cases, "remote", "bad"]},
        results,
    )
    monkeypatch.setattr(workers_module, "_capture_readonly", runner)

    views = {view.name: view for view in workers_module.collect_worker_views(home)}

    assert all(view.herdr_status == "unknown" for view in views.values())
    assert "absent" not in views
    assert "failed" in views
    assert views["failed"].herdr_note == "Herdr status unavailable"
    assert views["timeout"].herdr_note == "Herdr status timed out"
    assert views["malformed"].herdr_note == "Herdr status unavailable"
    assert views["result-malformed"].herdr_note == "Herdr status unavailable"
    assert views["agent-malformed"].herdr_note == "Herdr status unavailable"
    assert views["status-malformed"].herdr_note == "Herdr status unavailable"
    assert views["error-malformed"].herdr_note == "Herdr status unavailable"
    assert views["identity-malformed"].herdr_note == "Herdr status unavailable"
    assert views["identity-empty"].herdr_note == "Herdr status unavailable"
    assert views["remote"].herdr_note == "remote Herdr status unavailable"
    assert views["bad"].herdr_note == "endpoint metadata unavailable"
    queried = {call[3] for call in calls if call[0] == "herdr"}
    assert queried == set(results)
    assert "w11:p1" not in queried and "w12:p1" not in queried


def test_task_row_count_mismatch_is_unavailable(tmp_path, monkeypatch):
    home = firstmate_home(tmp_path)
    (home / "state/current.meta").write_text(herdr_meta("current", "fleet", "w1:p1"))

    def mismatched(argv, *, timeout, env=None):
        if argv[0] == "tasks-axi":
            return workers_module.CommandResult(
                "tasks[1]{id,state,kind,repo,title}:\n"
                "  current,working,ship,repo,First title\n"
                "  other,done,ship,repo,Second title\n", 0,
            )
        raise AssertionError("status lookup should not run")

    monkeypatch.setattr(workers_module, "_capture_readonly", mismatched)

    with pytest.raises(workers_module.WorkersUnavailable, match="task records"):
        workers_module.worker_messages(home)


def test_duplicate_task_rows_are_unavailable(tmp_path, monkeypatch):
    home = firstmate_home(tmp_path)
    (home / "state/current.meta").write_text(herdr_meta("current", "fleet", "w1:p1"))

    def duplicate(argv, *, timeout, env=None):
        if argv[0] == "tasks-axi":
            return workers_module.CommandResult(
                "tasks[2]{id,state,kind,repo,title}:\n"
                "  current,working,ship,repo,First title\n"
                "  current,done,ship,repo,Second title\n", 0,
            )
        raise AssertionError("status lookup should not run")

    monkeypatch.setattr(workers_module, "_capture_readonly", duplicate)

    with pytest.raises(workers_module.WorkersUnavailable, match="task records"):
        workers_module.worker_messages(home)


def test_firstmate_paths_reject_symlinked_parent(tmp_path):
    home = firstmate_home(tmp_path)
    real_bin = home / "real-bin"
    real_bin.mkdir()
    (real_bin / "fm-crew-state.sh").write_text("#!/bin/sh\nexit 0\n")
    (real_bin / "fm-crew-state.sh").chmod(0o700)
    (home / "bin/fm-crew-state.sh").unlink()
    (home / "bin").rmdir()
    (home / "bin").symlink_to(real_bin, target_is_directory=True)

    with pytest.raises(workers_module.WorkersUnavailable):
        workers_module.collect_worker_views(home)


def test_malformed_task_rows_are_unavailable(tmp_path, monkeypatch):
    home = firstmate_home(tmp_path)
    (home / "state/current.meta").write_text(herdr_meta("current", "fleet", "w1:p1"))

    def malformed(argv, *, timeout, env=None):
        if argv[0] == "tasks-axi":
            return workers_module.CommandResult(
                "tasks[1]{id,state,kind,repo,title}:\n"
                "  invalid id,done,ship,repo,Task\n", 0,
            )
        raise AssertionError("status lookup should not run")

    monkeypatch.setattr(workers_module, "_capture_readonly", malformed)

    with pytest.raises(workers_module.WorkersUnavailable, match="task records"):
        workers_module.worker_messages(home)


def test_unavailable_task_query_does_not_claim_stale_workers(tmp_path, monkeypatch):
    home = firstmate_home(tmp_path)
    (home / "state/stale.meta").write_text(herdr_meta("stale", "fleet", "w1:p1"))

    def unavailable(argv, *, timeout, env=None):
        return workers_module.CommandResult("", 127)

    monkeypatch.setattr(workers_module, "_capture_readonly", unavailable)

    with pytest.raises(workers_module.WorkersUnavailable, match="task records"):
        workers_module.worker_messages(home)


def test_reused_herdr_endpoint_is_excluded(tmp_path, monkeypatch):
    home = firstmate_home(tmp_path)
    (home / "state/reused.meta").write_text(herdr_meta("reused", "fleet", "w1:p1"))
    runner, _ = fake_runner_for(
        {"reused": "Reused task"},
        {"w1:p1": workers_module.CommandResult(
            '{"result":{"agent":{"task_id":"different-task",'
            '"agent_status":"working"}}}', 0,
        )},
    )
    monkeypatch.setattr(workers_module, "_capture_readonly", runner)

    assert workers_module.worker_messages(home) == ["No Firstmate task records."]


def test_stale_status_history_is_not_read_and_current_state_failures_are_bounded(tmp_path, monkeypatch):
    home = firstmate_home(tmp_path)
    (home / "state/current.meta").write_text(herdr_meta("current", "fleet", "w1:p1"))
    (home / "state/current.status").write_text("needs-decision: stale historical gate\n")
    runner, _ = fake_runner_for(
        {"current": "Current task"}, {"w1:p1": "working"},
        current={"current": workers_module.CommandResult("garbage", 0)},
    )
    monkeypatch.setattr(workers_module, "_capture_readonly", runner)
    view = workers_module.collect_worker_views(home)[0]
    assert view.progress == "unknown"
    assert view.progress_note == "current-state response malformed"
    assert "stale historical gate" not in workers_module.worker_messages(home)[0]


def test_output_spans_messages_without_dropping_workers(tmp_path, monkeypatch):
    home = firstmate_home(tmp_path)
    titles = {}
    native = {}
    for index in range(45):
        name = f"worker-{index:02d}"
        pane = f"w{index}:p1"
        (home / "state" / f"{name}.meta").write_text(herdr_meta(name, "fleet", pane))
        titles[name] = "A concrete task description " + ("x" * 120)
        native[pane] = "idle"
    runner, _ = fake_runner_for(titles, native)
    monkeypatch.setattr(workers_module, "_capture_readonly", runner)

    messages = workers_module.worker_messages(home, limit=700)

    assert len(messages) > 1
    assert all(len(message.encode("utf-16-le")) // 2 <= 700 for message in messages)
    combined = "\n".join(messages)
    for name in titles:
        assert combined.count(f"{name} -") == 1


def test_package_load_and_status_work_without_firstmate_import(tmp_path, monkeypatch):
    class BlockFirstmateImport:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "pi_telegram_workers":
                raise AssertionError("Firstmate integration imported during startup")
            return None

    monkeypatch.setattr(sys, "meta_path", [BlockFirstmateImport(), *sys.meta_path])
    bot = load_path("pi_telegram_workers_standalone_bot", BOT)
    calls = []

    class FakeApi:
        async def call(self, method, params=None, timeout=30):
            calls.append((method, params))
            return {"message_id": len(calls)}

    class FakeClient:
        def write(self, frame):
            pass

        async def drain(self):
            pass

    mirror = bot.MirrorBot(
        bot.Config(tmp_path, "token", 7, 8, "transcribe", "fake"), FakeApi()
    )
    mirror.client = FakeClient()
    mirror.client_ready = True
    mirror.session_root = None
    asyncio.run(mirror.handle_update({"message": {
        "message_id": 1,
        "from": {"id": 7},
        "chat": {"id": 8, "type": "private"},
        "text": "/telegram_status",
    }}))
    assert calls[-1][1]["text"].startswith("Mirror:")


def test_pairing_denial_and_standalone_mode_never_query_workers(tmp_path, monkeypatch):
    bot = load_path("pi_telegram_workers_bot", BOT)
    calls = []

    class FakeApi:
        async def call(self, method, params=None, timeout=30):
            calls.append((method, params))
            return {"message_id": len(calls)}

    invoked = []
    monkeypatch.setattr(bot, "worker_messages", lambda home: invoked.append(home) or ["workers"])
    mirror = bot.MirrorBot(
        bot.Config(tmp_path, "token", 7, 8, "transcribe", "fake"), FakeApi()
    )
    mirror.client = object()
    mirror.client_ready = True
    mirror.session_root = tmp_path

    asyncio.run(mirror.handle_update({"message": {
        "message_id": 1,
        "from": {"id": 99},
        "chat": {"id": 8, "type": "private"},
        "text": "/workers",
    }}))
    assert not invoked and not calls

    mirror.client = None
    mirror.session_root = None
    asyncio.run(mirror.handle_update({"message": {
        "message_id": 2,
        "from": {"id": 7},
        "chat": {"id": 8, "type": "private"},
        "text": "/workers",
    }}))
    assert not invoked
    assert calls[-1][1]["text"] == "Workers unavailable: no connected Firstmate home."
    assert not mirror.queue


def test_owner_helper_reveals_only_the_live_exact_peers_root(tmp_path, monkeypatch, capsys):
    owner = load_path("pi_telegram_workers_owner", OWNER)
    record = {"pid": 12, "uid": 34, "root": str(tmp_path)}
    monkeypatch.setattr(owner, "read_record", lambda: record)
    monkeypatch.setattr(owner, "alive", lambda candidate: candidate is record)

    assert owner.session_root(12, 34) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path.resolve())
    assert owner.session_root(13, 34) == 1
    assert owner.session_root(12, 35) == 1
    assert capsys.readouterr().out == ""


def test_snapshot_identity_is_safe_and_distinguishes_homes(tmp_path):
    bot = load_path("pi_telegram_workers_snapshot_identity_bot", BOT)
    first = tmp_path / "GitHub" / "firstmate"
    second = tmp_path / "Other" / "firstmate"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    assert bot.snapshot_identity(first) == "Snapshot: GitHub/firstmate"
    assert bot.snapshot_identity(second) == "Snapshot: Other/firstmate"
    unsafe = tmp_path / "GitHub<\x01>" / "first\x02mate"
    unsafe.mkdir(parents=True)
    identity = bot.snapshot_identity(unsafe)
    assert identity.startswith("Snapshot: GitHub/")
    assert "first-mate" in identity
    assert str(tmp_path) not in identity
    assert "\x01" not in identity
    assert bot.snapshot_identity(tmp_path / "missing") == (
        "Snapshot: Firstmate home unavailable"
    )


def test_workers_snapshot_survives_session_switch(tmp_path, monkeypatch):
    bot = load_path("pi_telegram_workers_snapshot_bot", BOT)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def delayed_workers(_home):
        started.set()
        assert release.wait(2)
        return ["old session workers"]

    class FakeApi:
        async def call(self, method, params=None, timeout=30, **kwargs):
            calls.append((method, params))
            return {"message_id": len(calls)}

    monkeypatch.setattr(bot, "worker_messages", delayed_workers)
    mirror = bot.MirrorBot(
        bot.Config(tmp_path, "token", 7, 8, "transcribe", "fake"), FakeApi()
    )
    mirror.client = object()
    mirror.client_ready = True
    mirror.session_root = tmp_path

    async def run_snapshot():
        task = asyncio.create_task(mirror.send_workers(reply_to=4))
        assert await asyncio.to_thread(started.wait, 2)
        mirror.session_root = tmp_path / "new-session"
        release.set()
        await task

    asyncio.run(run_snapshot())
    sent = [params["text"] for method, params in calls if method == "sendMessage"]
    assert sent == [
        f"{bot.snapshot_identity(tmp_path)}\n\nold session workers"
    ]


def test_telegram_workers_uses_connected_herdr_binding_and_preserves_pairing(tmp_path, monkeypatch):
    bot = load_path("pi_telegram_workers_bot_herdr_binding", BOT)
    calls = []
    worker_calls = []

    class FakeApi:
        async def call(self, method, params=None, timeout=30, **kwargs):
            calls.append((method, params))
            return {"message_id": len(calls)}

    def fake_workers(home, **binding):
        worker_calls.append((home, binding))
        return ["Herdr workspaces\n\nFirstmate - primary"]

    monkeypatch.setattr(bot, "worker_messages", fake_workers)
    mirror = bot.MirrorBot(
        bot.Config(tmp_path, "token", 7, 8, "transcribe", "fake"), FakeApi()
    )
    mirror.client = object()
    mirror.session_root = tmp_path

    async def exercise():
        await mirror.handle_frame({
            "t": "hello", "features": [],
            "herdr_socket_path": "/private/herdr/sessions/connected/herdr.sock",
            "herdr_workspace_id": "w1",
        })
        await mirror.handle_update({"message": {
            "message_id": 9, "from": {"id": 7},
            "chat": {"id": 8, "type": "private"}, "text": "/workers",
        }})
        await mirror.handle_update({"message": {
            "message_id": 10, "from": {"id": 99},
            "chat": {"id": 8, "type": "private"}, "text": "/workers",
        }})

    asyncio.run(exercise())
    assert worker_calls == [(
        tmp_path,
        {
            "herdr_socket_path": "/private/herdr/sessions/connected/herdr.sock",
            "herdr_workspace_id": "w1",
        },
    )]
    sent = [params for method, params in calls if method == "sendMessage"]
    assert len(sent) == 1 and sent[0]["reply_parameters"]["message_id"] == 9


def test_workers_command_uses_safe_transport_menu_and_multiple_messages(tmp_path, monkeypatch):
    bot = load_path("pi_telegram_workers_bot_messages", BOT)
    calls = []

    class FakeApi:
        async def call(self, method, params=None, timeout=30, **kwargs):
            calls.append((method, params))
            return {"message_id": len(calls)}

    monkeypatch.setattr(bot, "worker_messages", lambda _home: ["page one", "page two"])
    mirror = bot.MirrorBot(
        bot.Config(tmp_path, "token", 7, 8, "transcribe", "fake"), FakeApi()
    )
    mirror.client = object()
    mirror.client_ready = True
    mirror.session_root = tmp_path
    asyncio.run(mirror.handle_update({"message": {
        "message_id": 3,
        "from": {"id": 7},
        "chat": {"id": 8, "type": "private"},
        "text": "/workers",
    }}))
    assert [params["text"] for method, params in calls if method == "sendMessage"] == [
        f"{bot.snapshot_identity(tmp_path)}\n\npage one", "page two"
    ]
    assert all(params["reply_parameters"]["message_id"] == 3 for _, params in calls)
    assert {entry["command"] for entry in bot.MENU_COMMANDS} >= {"workers"}
    assert not mirror.queue
