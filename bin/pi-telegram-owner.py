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
    try:
        if HOME.is_symlink(): raise OSError("state directory is a symlink")
        HOME.mkdir(mode=0o700, exist_ok=True)
        if not HOME.is_dir(): raise OSError("state path is not a directory")
        HOME.chmod(0o700)
    except OSError:
        raise
def config():
    try:
        stat = CONFIG.lstat()
        if (stat.st_mode & 0o170000 != 0o100000 or stat.st_mode & 0o077 or
                stat.st_uid != getattr(os, "getuid", lambda: -1)()):
            return {}
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError): return {}
def write_config(data):
    if CONFIG.is_symlink(): raise OSError("configuration is a symlink")
    temporary = CONFIG.with_name(".config.tmp.%s" % secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            fd = -1
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, CONFIG)
    except Exception:
        if fd >= 0: os.close(fd)
        try: temporary.unlink()
        except OSError: pass
        raise
def roots(): return [str(Path(x).expanduser().resolve(strict=False)) for x in config().get("allowed_roots", []) if isinstance(x,str)]
def identity(pid):
    try:
        if sys.platform == "darwin":
            out=subprocess.check_output(["ps","-o","lstart=","-p",str(pid)], text=True, stderr=subprocess.DEVNULL).strip()
            return out
        raw=Path(f"/proc/{pid}/stat").read_text(); tail=raw[raw.rfind(")")+2:].split(); return tail[19]
    except (OSError, subprocess.SubprocessError, IndexError): return ""
def process_state(pid):
    try:
        if sys.platform == "darwin":
            return subprocess.check_output(["ps", "-o", "state=", "-p", str(pid)], text=True, stderr=subprocess.DEVNULL).strip()
        raw = Path(f"/proc/{pid}/stat").read_text()
        return raw[raw.rfind(")") + 2:].split()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""
def alive(record):
    try:
        pid = int(record["pid"])
        state = process_state(pid)
        return (pid > 0 and state and not state.startswith("Z") and os.kill(pid,0) is None and
                int(record["uid"]) == getattr(os,"getuid",lambda: -1)() and
                record["start"] == identity(pid) and
                Path(record["root"]).resolve(strict=False) in map(Path, roots()))
    except (KeyError, ValueError, OSError): return False
def read_record():
    try:
        if RECORD.is_symlink() or RECORD.stat().st_mode & 0o077: return None
        return json.loads(RECORD.read_text())
    except (OSError, ValueError): return None
def acquire_guard():
    token = secrets.token_hex(16)
    try:
        fd=os.open(GUARD, os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd, "w") as stream: stream.write(token)
        return token
    except FileExistsError:
        try:
            if time.time()-GUARD.stat().st_mtime <= 30: return ""
            stale = GUARD.with_name(".session.stale.%s" % secrets.token_hex(8))
            os.rename(GUARD, stale)
            stale.unlink()
            return acquire_guard()
        except OSError:
            return ""
def release(token):
    try:
        if GUARD.read_text() == token: GUARD.unlink()
    except OSError: pass
def is_requester(pid):
    if pid != os.getppid(): return False
    try:
        return getattr(os, "getuid", lambda: -1)() >= 0 and identity(pid) != ""
    except OSError:
        return False

def claim(cwd, owner_pid=None):
    secure(); root=str(Path(cwd).resolve(strict=True))
    if root not in roots(): return 1
    guard = acquire_guard()
    if not guard: return 1
    try:
        pid = os.getpid() if owner_pid is None else int(owner_pid)
        uid = getattr(os, "getuid", lambda: -1)()
        old=read_record()
        if old and alive(old) and not (int(old.get("pid",-1))==pid and old.get("root")==root): return 1
        if pid <= 0 or not is_requester(pid): return 1
        record={"pid":pid,"uid":uid,"start":identity(pid),"root":root,"incarnation":secrets.token_hex(16)}
        tmp=RECORD.with_name(".session.tmp.%s"%secrets.token_hex(8)); tmp.write_text(json.dumps(record)+"\n"); tmp.chmod(0o600); os.replace(tmp,RECORD); return 0
    finally: release(guard)
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
        d["allowed_roots"] = rs; write_config(d); return 0
    return claim(a.cwd, a.pid) if a.cmd == "claim" else check(a.cwd) if a.cmd == "check" else check_peer(a.pid,a.uid)
if __name__=="__main__": sys.exit(main())
