#!/usr/bin/env python3
"""Private exact-root Pi session eligibility and ownership helper."""
from __future__ import annotations
import argparse, json, os, secrets, signal, subprocess, sys, time
from pathlib import Path

HOME = Path(os.environ.get("PI_TELEGRAM_DIR", Path.home()/".pi-telegram")).expanduser()
CONFIG = HOME/"config.json"
RECORD = HOME/"session.json"
GUARD = HOME/"session.lock"

def secure():
    HOME.mkdir(mode=0o700, exist_ok=True); HOME.chmod(0o700)
def config():
    try: return json.loads(CONFIG.read_text())
    except (OSError, ValueError): return {}
def roots(): return [str(Path(x).expanduser().resolve(strict=False)) for x in config().get("allowed_roots", []) if isinstance(x,str)]
def identity(pid):
    try:
        if sys.platform == "darwin":
            out=subprocess.check_output(["ps","-o","lstart=","-p",str(pid)], text=True, stderr=subprocess.DEVNULL).strip()
            return out
        raw=Path(f"/proc/{pid}/stat").read_text(); tail=raw[raw.rfind(")")+2:].split(); return tail[19]
    except (OSError, subprocess.SubprocessError, IndexError): return ""
def alive(record):
    try:
        return (int(record["pid"]) > 0 and os.kill(int(record["pid"]),0) is None and
                int(record["uid"]) == getattr(os,"getuid",lambda: -1)() and
                record["start"] == identity(int(record["pid"])) and
                Path(record["root"]).resolve(strict=False) in map(Path, roots()))
    except (KeyError, ValueError, OSError): return False
def read_record():
    try:
        if RECORD.is_symlink() or RECORD.stat().st_mode & 0o077: return None
        return json.loads(RECORD.read_text())
    except (OSError, ValueError): return None
def acquire_guard():
    try:
        fd=os.open(GUARD, os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); os.close(fd); return True
    except FileExistsError:
        try:
            if time.time()-GUARD.stat().st_mtime > 30: GUARD.unlink(); return acquire_guard()
        except OSError: pass
        return False
def release():
    try: GUARD.unlink()
    except OSError: pass
def is_pi_process(pid):
    if pid != os.getppid(): return False
    try:
        if sys.platform == "darwin":
            command = subprocess.check_output(["ps", "-o", "command=", "-p", str(pid)], text=True, stderr=subprocess.DEVNULL)
        else:
            command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\\0", b" ").decode()
        parts = command.split()
        executable = Path(parts[0]).name if parts else ""
        return executable in {"pi", "pi.js", "pi.mjs"} or "pi-coding-agent" in command
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return False

def claim(cwd, owner_pid=None):
    secure(); root=str(Path(cwd).resolve(strict=True))
    if root not in roots(): return 1
    if not acquire_guard(): return 1
    try:
        pid = os.getpid() if owner_pid is None else int(owner_pid)
        uid = getattr(os, "getuid", lambda: -1)()
        old=read_record()
        if old and alive(old) and not (int(old.get("pid",-1))==pid and old.get("root")==root): return 1
        if pid <= 0 or not is_pi_process(pid) or identity(pid) == "": return 1
        record={"pid":pid,"uid":uid,"start":identity(pid),"root":root,"incarnation":secrets.token_hex(16)}
        tmp=RECORD.with_name(".session.tmp.%s"%secrets.token_hex(8)); tmp.write_text(json.dumps(record)+"\n"); tmp.chmod(0o600); os.replace(tmp,RECORD); return 0
    finally: release()
def check(cwd):
    try: return 0 if (str(Path(cwd).resolve(strict=True)) in roots() and (r:=read_record()) and alive(r) and int(r["pid"])==os.getpid()) else 1
    except OSError: return 1
def check_peer(pid,uid):
    r=read_record(); return 0 if r and alive(r) and int(r["pid"])==pid and int(r["uid"])==uid else 1
def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
    for n in ("claim","check"):
        q=sub.add_parser(n); q.add_argument("cwd")
        if n == "claim": q.add_argument("pid", nargs="?", type=int)
    q=sub.add_parser("allow-root"); q.add_argument("root")
    q=sub.add_parser("check-peer"); q.add_argument("pid",type=int); q.add_argument("uid",type=int)
    a=p.parse_args(); secure()
    if a.cmd=="allow-root":
        root=str(Path(a.root).expanduser().resolve(strict=True)); d=config(); rs=roots();
        if root not in rs: rs.append(root)
        d["allowed_roots"]=rs; CONFIG.write_text(json.dumps(d,indent=2)+"\n"); CONFIG.chmod(0o600); return 0
    return claim(a.cwd, a.pid) if a.cmd == "claim" else check(a.cwd) if a.cmd == "check" else check_peer(a.pid,a.uid)
if __name__=="__main__": sys.exit(main())
