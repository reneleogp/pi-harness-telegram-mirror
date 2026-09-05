import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
BOT = ROOT / "bin/pi-telegram.py"
OWNER = ROOT / "bin/pi-telegram-owner.py"


def load_bot():
    spec = importlib.util.spec_from_file_location("pi_telegram_matrix", BOT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_package_root_is_an_installed_runtime_boundary():
    result = subprocess.run([sys.executable, str(BOT), "package-root"], capture_output=True, text=True)
    assert result.returncode == 0
    assert Path(result.stdout.strip()).resolve() == ROOT.resolve()


def test_ineligible_session_cannot_create_ownership_record(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "project"
    root.mkdir()
    env = {**os.environ, "PI_TELEGRAM_DIR": str(home)}
    assert subprocess.run([sys.executable, str(OWNER), "allow-root", str(root)], env=env).returncode == 0
    config = json.loads((home / "config.json").read_text())
    config["pi_executable"] = str(tmp_path / "not-pi")
    (home / "config.json").write_text(json.dumps(config))
    (home / "config.json").chmod(0o600)
    result = subprocess.run([sys.executable, str(OWNER), "claim", str(root), str(os.getpid())], env=env)
    assert result.returncode != 0
    assert not (home / "session.json").exists()


def test_non_macos_service_request_fails_without_side_effect(tmp_path):
    if sys.platform == "darwin":
        return
    env = {**os.environ, "PI_TELEGRAM_DIR": str(tmp_path / "home")}
    result = subprocess.run([sys.executable, str(BOT), "service-unit"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "macOS only" in result.stderr


def test_rejected_delivery_retries_once_then_reports_drop():
    bot = load_bot()
    mirror = bot.MirrorBot(bot.Config(Path("/tmp"), "token", 1, 1, "transcribe", "http://fake"), bot.TelegramApi("http://fake", "token"))
    sent = []
    pumped = []
    async def fake_send(text, reply_to=None, **_kwargs):
        sent.append((text, reply_to))
    async def fake_pump():
        pumped.append(True)
    mirror.send = fake_send
    mirror.pump = fake_pump
    item = bot.Queued("m1", "hello", 42)
    mirror.pending[item.id] = item
    asyncio.run(mirror.on_rejected(item.id))
    assert len(mirror.queue) == 1 and len(pumped) == 1
    mirror.pending[item.id] = mirror.queue.popleft()
    asyncio.run(mirror.on_rejected(item.id))
    assert not mirror.pending and not mirror.queue
    assert len(sent) == 2


def test_migration_voice_ack_waits_for_pi_acceptance():
    bot = load_bot()
    mirror = bot.MirrorBot(bot.Config(Path("/tmp"), "token", 1, 1, "transcribe", "http://fake"), bot.TelegramApi("http://fake", "token"))
    mirror.migration_nonce = "n"
    mirror.migration_receipts["n"] = {"text", "image"}
    writes = []
    async def fake_send(*_args, **_kwargs):
        return {"message_id": 10}
    async def fake_edit(*_args, **_kwargs):
        return True
    async def fake_answer(*_args, **_kwargs):
        return None
    async def fake_write(frame):
        writes.append(frame)
        return True
    mirror.send = fake_send
    mirror.edit_card = fake_edit
    mirror.answer_callback = fake_answer
    mirror.write_frame = fake_write
    asyncio.run(mirror.handle_frame({"t": "migration_voice", "nonce": "n", "text": "transcript"}))
    assert 10 in mirror.voices
    asyncio.run(mirror.handle_callback({"id": "c", "data": "v:10:1:send", "from": {"id": 1}, "message": {"chat": {"id": 1}}}))
    assert not any(frame.get("t") == "migration_ack" for frame in writes)
    delivery_id = next(iter(mirror.queue)).id
    mirror.pending[delivery_id] = mirror.queue.popleft()
    asyncio.run(mirror.on_accepted(delivery_id))
    assert any(frame.get("t") == "migration_ack" for frame in writes)
