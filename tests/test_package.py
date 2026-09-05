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
  pi = Path(t) / 'pi-coding-agent'
  pi.write_text('import os, subprocess, sys, time\n'
                'r=subprocess.run([sys.executable, sys.argv[1], "claim", sys.argv[2], str(os.getpid())], env=os.environ)\n'
                'sys.exit(r.returncode)\n')
  child = subprocess.run([sys.executable, str(pi), str(OWNER), str(root)], env=e)
  assert child.returncode == 0
  assert json.loads((h/'session.json').read_text())['root']==str(root.resolve())
def test_permissions_and_no_secret_in_unit():
 with tempfile.TemporaryDirectory() as t:
  h=Path(t)/'h'; e={**os.environ,'PI_TELEGRAM_DIR':str(h)}; (h/'env').parent.mkdir(); (h/'env').write_text('TELEGRAM_BOT_TOKEN=secret\n'); (h/'env').chmod(0o600)
  out=subprocess.check_output([sys.executable,str(ROOT/'bin/pi-telegram.py'),'service-unit'],env=e,text=True)
  assert 'secret' not in out and 'TELEGRAM_BOT_TOKEN' not in out
  assert (h/'env').stat().st_mode & 0o077 == 0
def test_help(): assert subprocess.run([sys.executable,str(ROOT/'bin/pi-telegram.py'),'--help'],capture_output=True).returncode==0
