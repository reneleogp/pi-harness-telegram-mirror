from __future__ import annotations
import json, os, plistlib, shlex, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).parents[1]
OWNER=ROOT/'bin/pi-telegram-owner.py'
def run(*args, env): return subprocess.run([sys.executable,str(OWNER),*args],env=env,capture_output=True,text=True)
def test_manifest():
 d=json.loads((ROOT/'package.json').read_text()); assert 'pi-package' in d['keywords']; assert d['license']=='MIT'; assert d['pi']['extensions']==['./extensions/telegram-mirror.ts']; assert (ROOT/'LICENSE').is_file(); assert '@earendil-works/pi-coding-agent' in d['peerDependencies']
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
