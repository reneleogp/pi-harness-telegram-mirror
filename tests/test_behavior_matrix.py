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


def test_service_unit_is_platform_native_and_secret_free(tmp_path):
    env = {**os.environ, "PI_TELEGRAM_DIR": str(tmp_path / "home")}
    result = subprocess.run([sys.executable, str(BOT), "service-unit"], env=env, capture_output=True, text=True)
    if sys.platform in ("darwin", "linux"):
        assert result.returncode == 0
        assert "TELEGRAM_BOT_TOKEN" not in result.stdout
        assert ("<plist" in result.stdout) if sys.platform == "darwin" else ("[Service]" in result.stdout)
    else:
        assert result.returncode != 0


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


def test_fifo_delivery_emits_ordered_frames_and_clears_on_acceptance():
    bot = load_bot()
    mirror = bot.MirrorBot(bot.Config(Path("/tmp"), "token", 1, 1, "transcribe", "http://fake"), bot.TelegramApi("http://fake", "token"))
    writes = []
    async def fake_write(frame):
        writes.append(frame)
        return True
    async def fake_send(*_args, **_kwargs):
        return None
    mirror.client = object()
    mirror.write_frame = fake_write
    mirror.send = fake_send
    async def exercise():
        await mirror.accept_text("one", 1)
        await mirror.accept_text("two", 2)
    asyncio.run(exercise())
    assert [frame["text"] for frame in writes] == ["one", "two"]
    assert list(mirror.pending) == ["m1", "m2"]
    asyncio.run(mirror.on_accepted("m1"))
    asyncio.run(mirror.on_accepted("m2"))
    assert not mirror.pending


def test_commands_update_shared_state_and_return_status():
    bot = load_bot()
    mirror = bot.MirrorBot(bot.Config(Path("/tmp"), "token", 1, 1, "transcribe", "http://fake"), bot.TelegramApi("http://fake", "token"))
    writes = []
    async def fake_write(frame):
        writes.append(frame)
        return True
    async def fake_send(*_args, **_kwargs):
        return None
    mirror.write_frame = fake_write
    mirror.send = fake_send
    asyncio.run(mirror.handle_frame({"t": "command", "id": 3, "command": "off"}))
    assert mirror.mirror_on is False
    assert any(frame.get("t") == "command_result" for frame in writes)
    assert any(frame.get("t") == "state" and frame.get("mirror") is False for frame in writes)


def test_image_contract_rejects_invalid_bytes_and_accepts_real_png():
    bot = load_bot()
    assert bot.accept_outbound_images([{"mime": "image/png", "data": "bm90LXBuZw=="}]) == ([], 1)
    png = __import__("base64").b64encode(bytes([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]) + b"rest").decode()
    accepted, refused = bot.accept_outbound_images([{"mime": "image/png", "data": png}])
    assert len(accepted) == 1 and refused == 0
