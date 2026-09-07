from __future__ import annotations
import json, os, plistlib, shlex, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).parents[1]
OWNER=ROOT/'bin/pi-telegram-owner.py'
def run(*args, env): return subprocess.run([sys.executable,str(OWNER),*args],env=env,capture_output=True,text=True)
def test_manifest():
 d=json.loads((ROOT/'package.json').read_text()); assert 'pi-package' in d['keywords']; assert d['license']=='MIT'; assert d['pi']['extensions']==['./extensions/telegram-mirror.ts']; assert (ROOT/'LICENSE').is_file(); assert '@earendil-works/pi-coding-agent' in d['peerDependencies']

def test_claude_points_to_project_guidance():
 assert (ROOT/'AGENTS.md').is_file()
 assert (ROOT/'CLAUDE.md').is_symlink()
 assert os.readlink(ROOT/'CLAUDE.md') == 'AGENTS.md'
def test_exact_root_and_contention():
 with tempfile.TemporaryDirectory() as t:
  h=Path(t)/'h'; root=Path(t)/'root'; root.mkdir(); sub=root/'sub'; sub.mkdir(); e={**os.environ,'PI_TELEGRAM_DIR':str(h)}
  assert run('allow-root',str(root),env=e).returncode==0
  assert run('claim',str(sub),env=e).returncode != 0
  assert run('claim',str(root),env=e).returncode != 0
  assert not (h/'session.json').exists()
def parse_systemd_unit(text):
 sections = {}; section = None
 for line in text.splitlines():
  if not line: continue
  if line.startswith('[') and line.endswith(']'):
   section = line[1:-1]; sections[section] = {}; continue
  assert section is not None and '=' in line
  key, value = line.split('=', 1)
  parsed = shlex.split(value, comments=False)
  sections[section].setdefault(key, []).append([item.replace('%%', '%') for item in parsed])
 return sections

def test_permissions_and_no_secret_in_unit():
 with tempfile.TemporaryDirectory() as t:
  h=Path(t)/'h'; e={**os.environ,'PI_TELEGRAM_DIR':str(h)}; (h/'env').parent.mkdir(); (h/'env').write_text('TELEGRAM_BOT_TOKEN=secret\n'); (h/'env').chmod(0o600)
  result=subprocess.run([sys.executable,str(ROOT/'bin/pi-telegram.py'),'service-unit'],env=e,capture_output=True,text=True)
  assert result.returncode == 0
  assert 'secret' not in result.stdout and 'TELEGRAM_BOT_TOKEN' not in result.stdout
  if sys.platform == 'darwin':
   unit = plistlib.loads(result.stdout.encode())
   assert unit['EnvironmentVariables']['PI_TELEGRAM_DIR'] == str(h)
  elif sys.platform == 'linux':
   unit = parse_systemd_unit(result.stdout)
   assert unit['Service']['Type'] == [['simple']]
   environments = unit['Service']['Environment']
   assert [f'PI_TELEGRAM_DIR={h}'] in environments
   assert [f"PATH={Path.home() / '.local' / 'bin'}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"] in environments
   assert unit['Install']['WantedBy'] == [['default.target']]
  assert (h/'env').stat().st_mode & 0o077 == 0
def test_help(): assert subprocess.run([sys.executable,str(ROOT/'bin/pi-telegram.py'),'--help'],capture_output=True).returncode==0


def test_telegram_footer_uses_theme_check_and_honest_states():
    script = r'''
import { formatTelegramFooter } from "./extensions/telegram-footer.ts";
const calls = [];
const theme = { fg: (color, text) => { calls.push([color, text]); return `<${color}>${text}</${color}>`; } };
if (formatTelegramFooter(theme, true, true) !== "telegram: <success>✓</success>") throw new Error("enabled footer");
if (formatTelegramFooter(theme, true, false) !== "telegram: off") throw new Error("disabled footer");
if (formatTelegramFooter(theme, false, true) !== "telegram: unavailable") throw new Error("disconnected footer");
if (calls.length !== 1 || calls[0][0] !== "success" || calls[0][1] !== "✓") throw new Error("theme was not used");
'''
    result = subprocess.run(
        ['node', '--experimental-strip-types', '--input-type=module', '-'],
        cwd=ROOT, input=script, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_extension_delivery_mode_starts_idle_turns_and_steers_when_busy():
    script = r'''
import { sendTelegramDelivery } from "./extensions/telegram-delivery.ts";
const calls = [];
const send = async (content, options) => calls.push({ content, options });
await sendTelegramDelivery(send, true, "text");
await sendTelegramDelivery(send, true, "caption", { data: "png", mime: "image/png" });
await sendTelegramDelivery(send, false, "text");
await sendTelegramDelivery(send, false, "caption", { data: "png", mime: "image/png" });
if (calls[0].options !== undefined || calls[1].options !== undefined) throw new Error("idle delivery did not start a turn");
if (calls[2].options?.deliverAs !== "steer" || calls[3].options?.deliverAs !== "steer") throw new Error("busy delivery did not steer");
if (calls[1].content[1].type !== "image" || calls[3].content[1].type !== "image") throw new Error("image delivery was lost");
'''
    result = subprocess.run(
        ['node', '--experimental-strip-types', '--input-type=module', '-'],
        cwd=ROOT, input=script, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
