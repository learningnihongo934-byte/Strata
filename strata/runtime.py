"""Starting VMs, the per-VM supervisor (setup keypress, clipboard sync, agent health) and the agent client."""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from . import core
from .core import StrataError

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- agent client

def agent(cfg, method, path, body=None, timeout=10):
    """Calls the Strata Agent inside Windows. Raises StrataError if it isn't reachable."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{cfg['agent_port']}{path}", data=data, method=method,
                                 headers={"X-Strata-Token": cfg["token"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raise StrataError(f"Windows helper error: HTTP {e.code}")
    except Exception:
        raise StrataError("Windows isn't ready yet. Wait until the desktop has loaded "
                          "(the first setup takes about 15-30 minutes), then try again.")


def run_command(name, cmd, shell="cmd", cwd=None, timeout=120):
    cfg = core.load(name)
    if not core.is_running(name):
        raise StrataError(f"'{name}' isn't running. Start it with: strata start {name}")
    return agent(cfg, "POST", "/run", {"cmd": cmd, "shell": shell, "cwd": cwd or "", "timeout": timeout},
                 timeout=timeout + 15)


def open_in_windows(name, target):
    return agent(core.load(name), "POST", "/open", {"target": target})


# ---------------------------------------------------------------- start

def start(name, headless=False):
    cfg = core.load(name)
    if core.is_running(name):
        return False
    problems = [c for c, ok, _ in core.doctor()[:3] if not ok]
    if problems:
        raise StrataError(f"Missing: {', '.join(problems)}. Run: strata doctor")
    for f in ("qemu.pid", "status.json", "supervisor.log"):
        try:
            os.remove(core.vm_path(name, f))
        except OSError:
            pass
    env = dict(os.environ, PYTHONPATH=PKG_ROOT)
    log = open(core.vm_path(name, "supervisor.log"), "ab")
    proc = subprocess.Popen([sys.executable, "-m", "strata", "_supervise", name] + (["--headless"] if headless else []),
                            cwd=PKG_ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                            start_new_session=True)
    for _ in range(100):
        if core.is_running(name):
            time.sleep(1.5)  # QEMU writes its pidfile before devices finish initializing
            if core.is_running(name):
                return True
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    tail = _tail(core.vm_path(name, "qemu.log")) or _tail(core.vm_path(name, "supervisor.log"))
    raise StrataError(f"'{name}' failed to start.\n{tail}")


def _tail(path, lines=8):
    try:
        with open(path, errors="replace") as f:
            return "".join(f.readlines()[-lines:]).strip()
    except OSError:
        return ""


# ---------------------------------------------------------------- supervisor

def _pbpaste():
    return subprocess.run(["pbpaste"], capture_output=True, env=dict(os.environ, LANG="en_US.UTF-8")).stdout.decode(
        "utf-8", "replace")


def _pbcopy(text):
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), env=dict(os.environ, LANG="en_US.UTF-8"))


def _press_through_boot_prompt(name):
    """Windows ISOs say "Press any key to boot from CD"; tap space for the first seconds of the very first boot."""
    for _ in range(100):
        if os.path.exists(core.vm_path(name, "monitor.sock")):
            break
        time.sleep(0.1)
    end = time.time() + 15
    while time.time() < end:
        try:
            core.monitor(name, "sendkey spc", timeout=2)
        except StrataError:
            pass
        time.sleep(0.4)


def supervise(name, headless=False):
    """Runs for the lifetime of one VM: launches swtpm + QEMU, then syncs clipboard and tracks the agent."""
    cfg = core.load(name)
    with open(core.vm_path(name, "supervisor.pid"), "w") as f:
        f.write(str(os.getpid()))
    core.start_tpm(name)
    qlog = open(core.vm_path(name, "qemu.log"), "wb")
    proc = subprocess.Popen(core.qemu_args(cfg, headless), stdin=subprocess.DEVNULL, stdout=qlog, stderr=qlog)

    first_boot = not cfg.get("installed") and not os.path.exists(core.vm_path(name, "booted-once"))
    if first_boot:
        open(core.vm_path(name, "booted-once"), "w").close()
        threading.Thread(target=_press_through_boot_prompt, args=(name,), daemon=True).start()

    last_clip = None
    connected = False
    next_ping = 0
    status = {}
    while proc.poll() is None:
        now = time.time()
        if now >= next_ping:
            try:
                info = agent(cfg, "GET", "/ping", timeout=3)
                if not connected:
                    connected = True
                    last_clip = None
                if not cfg.get("installed"):
                    cfg = core.update(name, installed=True)
                status = {"agent": True, "share": bool(info.get("share")), "hostname": info.get("hostname"),
                          "since": status.get("since") or int(now)}
            except StrataError:
                connected = False
                status = {"agent": False, "phase": "running" if cfg.get("installed") else "installing"}
            core.write_status(name, **status)
            next_ping = now + (5 if connected else 3)

        if connected and core.load(name).get("clipboard", True):
            try:
                mac = _pbpaste()
                if last_clip is None:
                    last_clip = mac
                elif mac != last_clip:
                    agent(cfg, "POST", "/clipboard", {"text": mac}, timeout=3)
                    last_clip = mac
                else:
                    win = agent(cfg, "GET", "/clipboard", timeout=3).get("text") or ""
                    if win and win != last_clip:
                        _pbcopy(win)
                        last_clip = win
            except StrataError:
                connected = False
        time.sleep(1)

    for f in ("status.json", "qemu.pid", "monitor.sock", "tpm.sock", "supervisor.pid"):
        try:
            os.remove(core.vm_path(name, f))
        except OSError:
            pass
