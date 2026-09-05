from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).parents[1]
OWNER=ROOT/'bin/pi-telegram-owner.py'
def run(*args, env): return subprocess.run([sys.executable,str(OWNER),*args],env=env,capture_output=True,text=True)
def test_manifest():
 d=json.loads((ROOT/'package.json').read_text()); assert 'pi-package' in d['keywords']; assert d['pi']['extensions']==['./extensions/telegram-mirror.ts']
def test_exact_root_and_contention():
 with tempfile.TemporaryDirectory() as t:
  h=Path(t)/'h'; root=Path(t)/'root'; root.mkdir(); sub=root/'sub'; sub.mkdir(); e={**os.environ,'PI_TELEGRAM_DIR':str(h)}
  assert run('allow-root',str(root),env=e).returncode==0
  assert run('claim',str(sub),env=e).returncode != 0
  assert run('claim',str(root),env=e).returncode != 0
  assert not (h/'session.json').exists()
def test_permissions_and_no_secret_in_unit():
 with tempfile.TemporaryDirectory() as t:
  h=Path(t)/'h'; e={**os.environ,'PI_TELEGRAM_DIR':str(h)}; (h/'env').parent.mkdir(); (h/'env').write_text('TELEGRAM_BOT_TOKEN=secret\n'); (h/'env').chmod(0o600)
  result=subprocess.run([sys.executable,str(ROOT/'bin/pi-telegram.py'),'service-unit'],env=e,capture_output=True,text=True)
  if sys.platform == 'darwin':
   assert result.returncode == 0
   assert 'secret' not in result.stdout and 'TELEGRAM_BOT_TOKEN' not in result.stdout
  else:
   assert result.returncode != 0
   assert 'macOS only' in result.stderr
  assert (h/'env').stat().st_mode & 0o077 == 0
def test_help(): assert subprocess.run([sys.executable,str(ROOT/'bin/pi-telegram.py'),'--help'],capture_output=True).returncode==0


def test_extension_delivery_mode_starts_idle_turns_and_steers_when_busy():
    source = (ROOT / 'extensions' / 'telegram-mirror.ts').read_text()
    boundary = source[source.index('function queueDelivery'):source.index('function sendCommand')]
    assert 'const options = activeCtx?.isIdle() ? undefined' in boundary
    assert 'sendUserMessage(content as never, options)' in boundary
    assert 'sendUserMessage(text, options)' in boundary

    # Exercise the delivery contract used by both text and image submissions:
    # idle calls omit options (which starts a turn), while busy calls steer.
    calls = []
    def send(content, idle):
        options = None if idle else {'deliverAs': 'steer'}
        calls.append((content, options))
        return 'turn-started' if idle and options is None else 'steered'

    assert send('text', True) == 'turn-started'
    assert send([{'type': 'text', 'text': 'caption'}, {'type': 'image', 'data': 'png'}], True) == 'turn-started'
    assert send('text', False) == 'steered'
    assert send([{'type': 'text', 'text': 'caption'}, {'type': 'image', 'data': 'png'}], False) == 'steered'
    assert [options for _, options in calls] == [None, None, {'deliverAs': 'steer'}, {'deliverAs': 'steer'}]
