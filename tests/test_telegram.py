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
    spec = importlib.util.spec_from_file_location("pi_telegram", BOT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def command(*args, env):
    return subprocess.run([sys.executable, str(OWNER), *args], env=env,
                          capture_output=True, text=True)


def test_unrelated_process_cannot_claim_allowed_root(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "project"
    root.mkdir()
    env = {**os.environ, "PI_TELEGRAM_DIR": str(home)}
    assert command("allow-root", str(root), env=env).returncode == 0
    assert command("claim", str(root), str(os.getpid()), env=env).returncode != 0
    assert not (home / "session.json").exists()


def test_migration_writes_usable_env_without_touching_legacy(tmp_path):
    legacy = tmp_path / "legacy"
    target = tmp_path / "target"
    legacy.mkdir()
    (legacy / "env").write_text("TELEGRAM_BOT_TOKEN=test-token\n")
    (legacy / "config.json").write_text(json.dumps({"user_id": 7, "chat_id": 8}))
    env = {**os.environ, "HOME": str(tmp_path), "PI_TELEGRAM_DIR": str(target)}
    result = subprocess.run([sys.executable, str(BOT), "migrate"], env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0

    conventional = tmp_path / ".firstmate-telegram"
    conventional.mkdir()
    (conventional / "env").write_text("TELEGRAM_BOT_TOKEN=test-token\n")
    (conventional / "config.json").write_text(json.dumps({"user_id": 7, "chat_id": 8}))
    result = subprocess.run([sys.executable, str(BOT), "migrate"], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert (target / "env").read_text() == "TELEGRAM_BOT_TOKEN=test-token\n"
    assert (legacy / "env").read_text() == "TELEGRAM_BOT_TOKEN=test-token\n"


def test_env_requires_private_regular_file(tmp_path):
    bot = load_bot()
    home = tmp_path / "home"
    home.mkdir()
    env = home / "env"
    env.write_text("TELEGRAM_BOT_TOKEN=secret\n")
    env.chmod(0o644)
    assert bot.read_env(home) == {}
    env.unlink()
    secret = tmp_path / "secret"
    secret.write_text("TELEGRAM_BOT_TOKEN=secret\n")
    env.symlink_to(secret)
    assert bot.read_env(home) == {}


def test_environment_token_is_not_an_authorized_source(tmp_path, monkeypatch):
    bot = load_bot()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret")
    try:
        bot.load_config(tmp_path / "home")
    except bot.TelegramError as error:
        assert "no TELEGRAM_BOT_TOKEN" in str(error)
    else:
        raise AssertionError("environment token was accepted")


def test_commands_and_image_validation_are_observable():
    bot = load_bot()
    config = bot.Config(Path("/tmp"), "token", 1, 1, "transcribe", "http://fake")
    mirror = bot.MirrorBot(config, bot.TelegramApi("http://fake", "token"))
    assert "Mirror is off" in mirror.apply_command("off")
    assert "Mirror is on" in mirror.apply_command("on")
    assert bot.sniff_image_mime(bytes([137]) + b"PNG\r\n\x1a\n") == "image/png"
    assert bot.sniff_image_mime(b"not-an-image") is None


def test_transcription_diagnostics_are_bounded(tmp_path):
    bot = load_bot()
    command = tmp_path / "noisy.py"
    command.write_text("import sys; sys.stderr.write('x' * 1000000); raise SystemExit(3)\n")
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"audio")
    try:
        bot.run_transcribe(f"{sys.executable} {command}", audio)
    except bot.TelegramError as error:
        assert len(str(error)) < 1000
    else:
        raise AssertionError("noisy transcription unexpectedly succeeded")


def test_oversized_client_frame_is_disconnected():
    bot = load_bot()
    config = bot.Config(Path("/tmp"), "token", 1, 1, "transcribe", "http://fake")
    mirror = bot.MirrorBot(config, bot.TelegramApi("http://fake", "token"))

    class Writer:
        def __init__(self):
            self.closed = False
        def is_closing(self):
            return self.closed
        def close(self):
            self.closed = True
        def write(self, _data):
            pass
        async def drain(self):
            pass

    async def exercise():
        bot.MAX_FRAME_BYTES = 32
        reader = asyncio.StreamReader(limit=32)
        reader.feed_data(b"x" * 100 + b"\n")
        reader.feed_eof()
        writer = Writer()
        original = bot.peer_owns_session_lock
        bot.peer_owns_session_lock = lambda _: True
        try:
            await mirror.handle_client(reader, writer)
        finally:
            bot.peer_owns_session_lock = original
        return writer.closed

    assert asyncio.run(exercise())
