import asyncio
import importlib.util
import http.server
import json
import os
import subprocess
import sys

import pytest
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
    assert command("claim", str(root), "1", env=env).returncode != 0
    assert not (home / "session.json").exists()


def test_migration_writes_usable_env_without_touching_legacy(tmp_path):
    legacy = tmp_path / "legacy"
    target = tmp_path / "target"
    legacy.mkdir()
    (legacy / "env").write_text("TELEGRAM_BOT_TOKEN=test-token\n")
    (legacy / "config.json").write_text(json.dumps({"user_id": 7, "chat_id": 8}))
    os.chmod(legacy / "env", 0o600)
    os.chmod(legacy / "config.json", 0o600)
    env = {**os.environ, "HOME": str(tmp_path), "PI_TELEGRAM_DIR": str(target)}
    result = subprocess.run([sys.executable, str(BOT), "migrate"], env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0

    conventional = tmp_path / ".firstmate-telegram"
    conventional.mkdir()
    target.mkdir(exist_ok=True)
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    (target / "env").symlink_to(outside)
    (conventional / "env").write_text("TELEGRAM_BOT_TOKEN=test-token\n")
    (conventional / "config.json").write_text(json.dumps({"user_id": 7, "chat_id": 8}))
    os.chmod(conventional / "env", 0o600)
    os.chmod(conventional / "config.json", 0o600)
    result = subprocess.run([sys.executable, str(BOT), "migrate"], env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert outside.read_text() == "untouched"
    (target / "env").unlink()
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
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    state = tmp_path / "state"
    state.symlink_to(redirected, target_is_directory=True)
    try:
        bot.private_dir(state)
    except bot.TelegramError:
        pass
    else:
        raise AssertionError("symlinked state directory was accepted")
    config_target = tmp_path / "config-target"
    config_target.write_text('{"user_id": 1}')
    (home / "config.json").symlink_to(config_target)
    assert bot.read_config(home) == {}


def test_environment_token_is_not_an_authorized_source(tmp_path, monkeypatch):
    bot = load_bot()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret")
    try:
        bot.load_config(tmp_path / "home")
    except bot.TelegramError as error:
        assert "no TELEGRAM_BOT_TOKEN" in str(error)
    else:
        raise AssertionError("environment token was accepted")


def test_reload_pi_terminal_routes_literal_reload_to_eligible_session(tmp_path):
    bot = load_bot()
    calls = []
    writes = []

    class FakeApi:
        async def call(self, method, params=None, timeout=30):
            calls.append((method, params))
            return {"message_id": len(calls)}

    mirror = bot.MirrorBot(
        bot.Config(tmp_path, "token", 7, 8, "transcribe", "fake"), FakeApi()
    )
    mirror.client = object()
    mirror.client_ready = True
    mirror.session_root = tmp_path

    async def fake_write(frame):
        writes.append(frame)
        return True

    mirror.write_frame = fake_write

    async def exercise():
        task = asyncio.create_task(mirror.handle_update({"message": {
            "message_id": 10,
            "from": {"id": 7},
            "chat": {"id": 8, "type": "private"},
            "text": "/reload-pi-terminal",
        }}))
        await asyncio.sleep(0)
        assert writes == [{"t": "command", "id": 1, "command": "reload"}]
        await mirror.handle_frame({
            "t": "command_result", "id": 1,
            "text": bot.RELOAD_PI_TERMINAL_REPLY,
        })
        await task

    asyncio.run(exercise())
    assert [params["text"] for method, params in calls if method == "sendMessage"] == [
        bot.RELOAD_PI_TERMINAL_REPLY,
    ]
    assert calls[-1][1]["reply_parameters"]["message_id"] == 10
    assert not mirror.queue and not mirror.pending


def test_reload_pi_terminal_menu_registration_uses_valid_scoped_commands(tmp_path):
    bot = load_bot()
    calls = []

    class FakeApi:
        async def call(self, method, params=None, timeout=30):
            calls.append((method, params))
            return True

    mirror = bot.MirrorBot(
        bot.Config(tmp_path, "token", 7, 8, "transcribe", "fake"), FakeApi()
    )
    asyncio.run(mirror.register_menu())
    assert calls[0][0] == "setMyCommands"
    params = calls[0][1]
    assert params["scope"] == {"type": "chat", "chat_id": 8}
    assert all(item["command"].isascii() and item["command"].replace("_", "").isalnum()
               and 1 <= len(item["command"]) <= 32 for item in params["commands"])
    commands = [item["command"] for item in params["commands"]]
    assert commands == [
        "token_usage", "agent_info", "workers",
        "telegram_on", "telegram_off", "telegram_status",
        "telegram_confirmations_on", "telegram_confirmations_off",
        "change_model", "change_thinking", "reload_pi_terminal",
    ]
    assert "reload-pi-terminal" not in commands


def test_reload_pi_terminal_alias_and_menu_registration_are_valid(tmp_path):
    bot = load_bot()
    labels = {item["command"]: item["description"] for item in bot.MENU_COMMANDS}
    assert labels["reload_pi_terminal"] == "Reload the connected Pi terminal"
    assert all("-" not in item["command"] for item in bot.MENU_COMMANDS)
    assert bot.RELOAD_PI_TERMINAL_ALIASES["/reload-pi-terminal"] == "reload"
    assert bot.RELOAD_PI_TERMINAL_ALIASES["/reload_pi_terminal"] == "reload"

    calls = []

    class FakeApi:
        async def call(self, method, params=None, timeout=30):
            calls.append((method, params))
            return {"message_id": len(calls)}

    mirror = bot.MirrorBot(
        bot.Config(tmp_path, "token", 7, 8, "transcribe", "fake"), FakeApi()
    )
    mirror.client = object()
    mirror.client_ready = True
    mirror.session_root = tmp_path
    writes = []

    async def fake_write(frame):
        writes.append(frame)
        return True

    mirror.write_frame = fake_write

    async def exercise():
        task = asyncio.create_task(mirror.handle_update({"message": {
            "message_id": 11,
            "from": {"id": 7},
            "chat": {"id": 8, "type": "private"},
            "text": "/reload_pi_terminal",
        }}))
        await asyncio.sleep(0)
        await mirror.handle_frame({
            "t": "command_result", "id": 1,
            "text": bot.RELOAD_PI_TERMINAL_REPLY,
        })
        await task

    asyncio.run(exercise())
    assert writes == [{"t": "command", "id": 1, "command": "reload"}]
    assert calls[-1][1]["text"] == bot.RELOAD_PI_TERMINAL_REPLY


def test_reload_pi_terminal_rejects_disconnected_unauthorized_and_malformed_messages(tmp_path):
    bot = load_bot()
    calls = []

    class FakeApi:
        async def call(self, method, params=None, timeout=30):
            calls.append((method, params))
            return {"message_id": len(calls)}

    mirror = bot.MirrorBot(
        bot.Config(tmp_path, "token", 7, 8, "transcribe", "fake"), FakeApi()
    )

    async def send(message_id, sender, chat, text):
        await mirror.handle_update({"message": {
            "message_id": message_id,
            "from": {"id": sender},
            "chat": {"id": chat, "type": "private"},
            "text": text,
        }})

    async def exercise():
        await send(1, 7, 8, "/reload-pi-terminal")
        await send(2, 99, 8, "/reload-pi-terminal")
        await send(3, 7, 999, "/reload-pi-terminal")
        await send(4, 7, 8, "/reload-pi-terminal now")

    asyncio.run(exercise())
    texts = [params["text"] for method, params in calls if method == "sendMessage"]
    assert texts == [bot.RELOAD_PI_TERMINAL_UNAVAILABLE, bot.RELOAD_PI_TERMINAL_USAGE]
    assert not mirror.queue and not mirror.pending


def test_reload_pi_terminal_duplicate_update_is_not_submitted_twice(tmp_path):
    bot = load_bot()
    calls = []
    writes = []

    class FakeApi:
        async def call(self, method, params=None, timeout=30):
            calls.append((method, params))
            return {"message_id": len(calls)}

    mirror = bot.MirrorBot(
        bot.Config(tmp_path, "token", 7, 8, "transcribe", "fake"), FakeApi()
    )
    mirror.client = object()
    mirror.client_ready = True
    mirror.session_root = tmp_path

    async def fake_write(frame):
        writes.append(frame)
        return True

    mirror.write_frame = fake_write
    update = {"message": {
        "message_id": 12,
        "from": {"id": 7},
        "chat": {"id": 8, "type": "private"},
        "text": "/reload-pi-terminal",
    }}

    async def exercise():
        first = asyncio.create_task(mirror.handle_update(update))
        await asyncio.sleep(0)
        await mirror.handle_frame({
            "t": "command_result", "id": 1,
            "text": bot.RELOAD_PI_TERMINAL_REPLY,
        })
        await first
        await mirror.handle_update(update)

    asyncio.run(exercise())
    assert writes == [{"t": "command", "id": 1, "command": "reload"}]
    assert len([1 for method, _ in calls if method == "sendMessage"]) == 1


def test_token_usage_routes_to_pi_without_queueing_conversation_text():
    bot = load_bot()
    mirror = bot.MirrorBot(bot.Config(Path("/tmp"), "token", 1, 1, "transcribe", "http://fake"), bot.TelegramApi("http://fake", "token"))
    writes = []
    async def fake_write(frame):
        writes.append(frame)
        return True
    mirror.write_frame = fake_write
    mirror.client = object()
    mirror.client_ready = True
    async def exercise():
        task = asyncio.create_task(mirror.request_pi_command(bot.TOKEN_USAGE_COMMAND))
        await asyncio.sleep(0)
        assert writes == [{"t": "command", "id": 1, "command": "token_usage"}]
        await mirror.handle_frame({"t": "command_result", "id": 1, "text": "GPT quota unavailable."})
        return await task
    assert asyncio.run(exercise()) == "GPT quota unavailable."
    assert not mirror.queue and not mirror.pending


def test_agent_control_commands_use_direct_framing_and_never_enter_message_queue(tmp_path):
    bot = load_bot()
    calls = []
    requests = []

    class FakeApi:
        async def call(self, method, params=None, timeout=30):
            calls.append((method, params))
            return {"message_id": len(calls)}

    mirror = bot.MirrorBot(bot.Config(tmp_path, "token", 7, 8, "transcribe", "fake"), FakeApi())

    async def fake_request(command, **values):
        requests.append((command, values))
        if command == "agent_info":
            return {"text": "Agent Info\nModel: one"}
        if command == "change_model":
            return {
                "text": "Change Model\nCurrent: alpha/one",
                "menu": "model",
                "choices": [
                    {"label": f"alpha/model-{index}", "provider": "alpha", "model": f"model-{index}"}
                    for index in range(10)
                ],
            }
        if command == "change_thinking":
            return {
                "text": "Change Thinking Level\nCurrent: low",
                "menu": "thinking",
                "target": {"provider": "alpha", "model": "one"},
                "choices": [{"label": "off", "level": "off"}, {"label": "low", "level": "low"}],
            }
        raise AssertionError(command)

    mirror.request_pi_result = fake_request

    async def exercise():
        messages = (
            "/agent_info", "/change_model", "/change_thinking",
            "/agent_info invalid", "/change_model invalid", "/change_thinking invalid",
        )
        for message_id, text in enumerate(messages, 1):
            await mirror.handle_update({"message": {
                "message_id": message_id,
                "from": {"id": 7},
                "chat": {"id": 8, "type": "private"},
                "text": text,
            }})

    asyncio.run(exercise())

    assert requests == [("agent_info", {}), ("change_model", {}), ("change_thinking", {})]
    labels = {item["command"]: item["description"] for item in bot.MENU_COMMANDS}
    assert labels["change_model"] == "Change Model"
    assert labels["change_thinking"] == "Change Thinking Level"
    assert labels["agent_info"] == "Agent Info"
    assert not mirror.queue and not mirror.pending
    menus = [call[1]["reply_markup"] for call in calls
             if call[0] == "sendMessage" and call[1].get("reply_markup")]
    assert len(menus[0]["inline_keyboard"]) == bot.CONTROL_PAGE_SIZE + 1
    assert menus[0]["inline_keyboard"][-1][-1]["text"] == "Next"
    assert calls[-1][1]["text"] == "Usage: /change_thinking"


def test_agent_control_callback_selects_from_bound_menu_and_refuses_stale_session(tmp_path):
    bot = load_bot()
    calls = []
    requests = []

    class FakeApi:
        async def call(self, method, params=None, timeout=30):
            calls.append((method, params))
            return {"message_id": 50}

    mirror = bot.MirrorBot(bot.Config(tmp_path, "token", 7, 8, "transcribe", "fake"), FakeApi())
    mirror.client = object()
    mirror.client_ready = True
    mirror._client_generation = 4

    async def fake_request(command, **values):
        requests.append((command, values))
        if values:
            return {"text": "Model changed\nModel: alpha/two\nThinking: low"}
        return {
            "text": "Change Model\nCurrent: alpha/one",
            "menu": "model",
            "choices": [
                {"label": "alpha/one", "provider": "alpha", "model": "one"},
                {"label": "alpha/two", "provider": "alpha", "model": "two"},
            ],
        }

    mirror.request_pi_result = fake_request

    async def exercise():
        await mirror.present_agent_control("change_model", 10)
        token, menu = next(iter(mirror.control_menus.items()))
        await mirror.handle_update({"callback_query": {
            "id": "callback-1",
            "from": {"id": 7},
            "message": {"message_id": menu.message_id, "chat": {"id": 8, "type": "private"}},
            "data": f"c:{token}:s:1",
        }})
        await mirror.present_agent_control("change_model", 11)
        stale_token, stale_menu = next(iter(mirror.control_menus.items()))
        mirror._client_generation += 1
        await mirror.handle_update({"callback_query": {
            "id": "callback-2",
            "from": {"id": 7},
            "message": {"message_id": stale_menu.message_id, "chat": {"id": 8, "type": "private"}},
            "data": f"c:{stale_token}:s:0",
        }})

    asyncio.run(exercise())

    assert requests[:2] == [
        ("change_model", {}),
        ("change_model", {"provider": "alpha", "model": "two"}),
    ]
    assert len(requests) == 3
    edits = [params for method, params in calls if method == "editMessageText"]
    assert edits[0]["text"].startswith("Model changed")
    answers = [params for method, params in calls if method == "answerCallbackQuery"]
    assert answers[-1]["text"] == bot.STALE_CONTROL_REPLY
    assert not mirror.queue and not mirror.pending


def test_agent_control_callbacks_require_the_paired_private_chat(tmp_path):
    bot = load_bot()
    calls = []

    class FakeApi:
        async def call(self, method, params=None, timeout=30):
            calls.append((method, params))
            return {"message_id": 1}

    mirror = bot.MirrorBot(bot.Config(tmp_path, "token", 7, 8, "transcribe", "fake"), FakeApi())
    menu = bot.ControlMenu("thinking", 1, "choose", [{"label": "off", "level": "off"}],
                           __import__("time").monotonic(), {"provider": "alpha", "model": "one"}, 20)
    mirror.control_menus["safe"] = menu
    mirror.client = object()
    mirror.client_ready = True
    mirror._client_generation = 1
    control_requests = []

    async def fake_request(command, **values):
        control_requests.append((command, values))
        return {"text": "unexpected"}

    mirror.request_pi_result = fake_request

    async def exercise():
        await mirror.handle_update({"callback_query": {
            "id": "callback",
            "from": {"id": 7},
            "message": {"message_id": 20, "chat": {"id": 8, "type": "group"}},
            "data": "c:safe:s:0",
        }})

    asyncio.run(exercise())
    assert calls == []
    assert control_requests == []


def test_agent_control_request_reply_framing_is_bound_to_connected_generation():
    bot = load_bot()
    mirror = bot.MirrorBot(bot.Config(Path("/tmp"), "token", 1, 1, "transcribe", "fake"),
                           bot.TelegramApi("fake", "token"))
    writes = []
    mirror.client = object()
    mirror.client_ready = True
    mirror._client_generation = 3

    async def fake_write(frame):
        writes.append(frame)
        return True

    mirror.write_frame = fake_write

    async def exercise():
        pending = asyncio.create_task(mirror.request_pi_result(
            "change_model", provider="alpha", model="two",
        ))
        await asyncio.sleep(0)
        assert writes == [{
            "t": "command", "id": 1, "command": "change_model",
            "provider": "alpha", "model": "two",
        }]
        await mirror.handle_frame({
            "t": "command_result", "id": 1,
            "text": "Model changed\nModel: alpha/two\nThinking: low",
        })
        return await pending

    result = asyncio.run(exercise())
    assert result["text"].startswith("Model changed")
    assert not mirror.command_waiters


def test_disconnected_agent_controls_and_token_usage_are_explicit():
    bot = load_bot()
    mirror = bot.MirrorBot(bot.Config(Path("/tmp"), "token", 1, 1, "transcribe", "fake"),
                           bot.TelegramApi("fake", "token"))

    async def exercise():
        control = await mirror.request_pi_result("agent_info")
        quota = await mirror.request_pi_command(bot.TOKEN_USAGE_COMMAND)
        return control, quota

    control, quota = asyncio.run(exercise())
    assert control == {"text": bot.AGENT_CONTROL_UNAVAILABLE}
    assert quota == bot.TOKEN_USAGE_UNAVAILABLE


def test_provider_quota_formatter_is_concise_and_human_readable():
    script = r'''import { formatProviderQuota } from "./extensions/telegram-quota.ts";
const now = new Date("2030-01-01T00:00:00.000Z");
const value = formatProviderQuota({ provider: "OpenAI Codex", windows: [
  { label: "weekly", remainingPercent: 3, resetAt: "2030-01-05T06:00:00.000Z" },
  { label: "5-hour", remainingPercent: 80, resetAt: "2030-01-01T00:35:00.000Z" },
] }, now);
if (value !== "GPT quota\nWeekly: 3% left · resets in 4d 6h\n5-hour: 80% left · resets in 35m") throw new Error(value);
if (value.includes("Provider") || value.includes("context") || value.includes("T00:00")) throw new Error(value);
if (formatProviderQuota(undefined) !== "GPT quota unavailable.") throw new Error("missing unavailable state");
'''
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-"], cwd=ROOT, input=script, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mirror_on", [True, False])
@pytest.mark.parametrize("confirmations", [True, False])
@pytest.mark.parametrize("command", ["/telegram_status", "/telegram status"])
def test_status_commands_dispatch_one_line_per_item_with_boolean_emojis(
    tmp_path, mirror_on, confirmations, command,
):
    bot = load_bot()
    calls = []

    class FakeApi:
        async def call(self, method, params=None, timeout=30):
            calls.append((method, params))
            return {"message_id": len(calls)}

    config = bot.Config(tmp_path, "fake-token", 7, 8, "transcribe", "fake")
    config.confirmations = confirmations
    mirror = bot.MirrorBot(config, FakeApi(), mirror_on=mirror_on)
    mirror.queue.append(bot.Queued("m1", "waiting", 42))

    async def exercise():
        await mirror.handle_update({
            "message": {
                "message_id": 10,
                "from": {"id": 7},
                "chat": {"id": 8, "type": "private"},
                "text": command,
            },
        })

    asyncio.run(exercise())

    assert len(calls) == 1
    assert calls[0][0] == "sendMessage"
    assert calls[0][1]["text"].splitlines() == [
        f"Mirror: {'✅' if mirror_on else '❌'}",
        "Pi: not running",
        f"Confirmations: {'✅' if confirmations else '❌'}",
        "Messages waiting: 1",
    ]
    assert "Mirror: on" not in calls[0][1]["text"]
    assert "Mirror: off" not in calls[0][1]["text"]
    assert "Confirmations: on" not in calls[0][1]["text"]
    assert "Confirmations: off" not in calls[0][1]["text"]


def test_commands_and_image_validation_are_observable():
    bot = load_bot()
    config = bot.Config(Path("/tmp"), "token", 1, 1, "transcribe", "http://fake")
    mirror = bot.MirrorBot(config, bot.TelegramApi("http://fake", "token"))
    assert "Mirror: ❌" in mirror.apply_command("off")
    assert "Mirror: ✅" in mirror.apply_command("on")
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


def test_parakeet_adapter_matches_v05_positional_cli(tmp_path):
    adapter = ROOT / "bin" / "pi-parakeet-mlx-transcribe.py"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_cli = fake_bin / "parakeet-mlx"
    fake_cli.write_text(f'''#!{sys.executable}
import pathlib
import sys

args = sys.argv[1:]
if not args or args[0] == "transcribe":
    print("Invalid value for 'audios': File 'transcribe' does not exist.", file=sys.stderr)
    raise SystemExit(2)
audio = pathlib.Path(args[0])
if not audio.is_file():
    raise SystemExit("audio was not first positional argument")
output_dir = pathlib.Path(args[args.index("--output-dir") + 1])
template = args[args.index("--output-template") + 1]
(output_dir / (template + ".txt")).write_text("synthetic CLI transcript\\n")
''')
    fake_cli.chmod(0o755)
    fake_ffmpeg = fake_bin / "ffmpeg"
    fake_ffmpeg.write_text(f"#!{sys.executable}\n")
    fake_ffmpeg.chmod(0o755)
    audio = tmp_path / "audio.ogg"
    audio.write_bytes(b"synthetic ogg")
    result = subprocess.run(
        [sys.executable, str(adapter), str(audio)],
        env={**os.environ, "PATH": str(fake_bin)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "synthetic CLI transcript"


def test_kernel_peer_credentials_are_observed():
    bot = load_bot()
    left, right = __import__("socket").socketpair()

    class Writer:
        def get_extra_info(self, name):
            return left if name == "socket" else None

    try:
        credentials = bot.peer_credentials(Writer())
        if credentials is not None:
            assert credentials[1] == os.getuid()
            assert credentials[0] > 0
    finally:
        left.close()
        right.close()


def test_fake_telegram_transport_executes_api_request():
    bot = load_bot()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            json.loads(self.rfile.read(length))
            body = b'{"ok":true,"result":{"accepted":true}}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = __import__("threading").Thread(target=server.serve_forever)
    thread.start()
    try:
        api = bot.TelegramApi(f"http://127.0.0.1:{server.server_port}", "token")
        assert api.request_sync("getMe", {"x": 1}) == {"accepted": True}
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_service_definition_contains_runtime_path(tmp_path):
    import plistlib

    bot = load_bot()
    original = bot.on_macos
    bot.on_macos = lambda: True
    try:
        service = plistlib.loads(bot.unit_text(tmp_path).encode())
    finally:
        bot.on_macos = original
    assert service["ProgramArguments"][1] == str(Path(bot.__file__).resolve())
    assert service["ProgramArguments"][2] == "run"
    assert service["EnvironmentVariables"]["PI_TELEGRAM_DIR"] == str(tmp_path)
    assert str(Path.home() / ".local" / "bin") in service["EnvironmentVariables"]["PATH"]


def test_reload_does_not_displace_the_connected_session_for_another_peer(tmp_path):
    bot = load_bot()
    mirror = bot.MirrorBot(
        bot.Config(tmp_path, "token", 1, 1, "transcribe", "fake"),
        bot.TelegramApi("http://fake", "token"),
    )

    class Writer:
        def __init__(self):
            self.closed = False
        def is_closing(self):
            return self.closed
        def close(self):
            self.closed = True

    first = Writer()
    second = Writer()
    mirror.client = first
    mirror.client_ready = True
    mirror.session_root = tmp_path
    async def exercise():
        reader = asyncio.StreamReader()
        reader.feed_eof()
        await mirror.handle_client(reader, second)

    original = bot.peer_owns_session_lock
    bot.peer_owns_session_lock = lambda _writer: True
    try:
        asyncio.run(exercise())
    finally:
        bot.peer_owns_session_lock = original
    assert mirror.client is first
    assert not first.closed
    assert second.closed


def test_real_unix_socket_delivers_protocol_state(tmp_path):
    bot = load_bot()
    config = bot.Config(tmp_path, "token", 1, 1, "transcribe", "http://fake")
    mirror = bot.MirrorBot(config, bot.TelegramApi("http://fake", "token"))

    async def exercise():
        path = Path("/tmp") / f"pi-test-{os.getpid()}.sock"
        server = await asyncio.start_unix_server(mirror.handle_client, path=str(path))
        original = bot.peer_owns_session_lock
        bot.peer_owns_session_lock = lambda _: True
        try:
            reader, writer = await asyncio.open_unix_connection(path=str(path))
            writer.write(b'{"t":"hello","features":[]}\n')
            await writer.drain()
            state = json.loads((await reader.readline()).decode())
            writer.close()
            await writer.wait_closed()
            assert state["t"] == "state"
        finally:
            bot.peer_owns_session_lock = original
            server.close()
            await server.wait_closed()
            path.unlink(missing_ok=True)

    asyncio.run(exercise())


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
