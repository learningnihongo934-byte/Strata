"""The local web dashboard: a small JSON API plus static files, bound to 127.0.0.1 only."""

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import core, runtime, windows
from .core import StrataError

PORT = int(os.environ.get("STRATA_PORT", "7650"))
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
TOKEN_FILE = os.path.join(core.HOME, "dashboard.token")
JOBS = {}  # name -> {"stage", "done", "total", "error"}
JOBS_LOCK = threading.Lock()
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


def dashboard_token():
    os.makedirs(core.HOME, exist_ok=True)
    if not os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "w") as f:
            f.write(secrets.token_hex(16))
        os.chmod(TOKEN_FILE, 0o600)
    with open(TOKEN_FILE) as f:
        return f.read().strip()


# ---------------------------------------------------------------- state

def vm_state(name):
    c = core.load(name)
    running = core.is_running(name)
    st = core.runtime_status(name)
    if not running:
        state = "stopped"
    elif st.get("agent"):
        state = "running"
    elif not c.get("installed"):
        state = "installing"
    else:
        state = "starting"
    return {
        "name": name, "state": state, "installed": c.get("installed", False),
        "cpus": c["cpus"], "memory_gb": c["memory_gb"], "disk_gb": c["disk_gb"],
        "disk_used": core.disk_usage_bytes(name), "share": c.get("share"),
        "share_mapped": st.get("share", False), "display": c.get("display", "basic"),
        "clipboard": c.get("clipboard", True), "username": c["username"], "created": c.get("created"),
    }


def full_state():
    with JOBS_LOCK:
        jobs = {k: dict(v) for k, v in JOBS.items()}
    vms = []
    for n in core.list_names():
        try:
            vms.append(vm_state(n))
        except Exception:
            pass
    return {
        "version": core.VERSION,
        "vms": vms,
        "jobs": jobs,
        "host": {"cpus": core.host_cpus(), "memory_gb": core.host_memory_gb(),
                 "free_disk": shutil.disk_usage(os.path.expanduser("~")).free,
                 "default_cpus": core.default_cpus(), "default_memory_gb": core.default_memory_gb()},
        "doctor": [{"check": c, "ok": ok, "hint": h} for c, ok, h in core.doctor()],
        "windows_downloaded": os.path.exists(windows.iso_path()),
    }


def create_job(body):
    share = body.get("share") or None
    cfg = core.new_config(body.get("name") or "windows", body.get("cpus"), body.get("memory_gb"),
                          body.get("disk_gb") or 64, share, body.get("username") or "User", body.get("password") or "")
    if cfg["name"] in JOBS:
        raise StrataError("That VM is already being created.")

    def progress(stage, done, total):
        with JOBS_LOCK:
            JOBS[cfg["name"]].update(stage=stage, done=done, total=total)

    def work():
        try:
            windows.create_vm(cfg, progress)
            if body.get("start", True):
                runtime.start(cfg["name"])
            with JOBS_LOCK:
                JOBS.pop(cfg["name"], None)
        except Exception as e:
            with JOBS_LOCK:
                JOBS[cfg["name"]].update(error=str(e))

    with JOBS_LOCK:
        JOBS[cfg["name"]] = {"stage": "Starting", "done": 0, "total": 0, "error": None}
    threading.Thread(target=work, daemon=True).start()
    return {"ok": True, "name": cfg["name"]}


def pick_folder():
    r = subprocess.run(["osascript", "-e",
                        'POSIX path of (choose folder with prompt "Choose a Mac folder to share with Windows")'],
                       capture_output=True, text=True)
    return {"path": r.stdout.strip().rstrip("/") or None}


def settings(name, body):
    changes = {}
    if "cpus" in body:
        changes["cpus"] = max(1, min(int(body["cpus"]), core.host_cpus() + 4))
    if "memory_gb" in body:
        m = int(body["memory_gb"])
        if m < 2 or m >= core.host_memory_gb():
            raise StrataError(f"Memory must be between 2 and {core.host_memory_gb() - 1} GB.")
        changes["memory_gb"] = m
    if "share" in body:
        s = body["share"] or None
        if s and not os.path.isdir(s):
            raise StrataError("That folder doesn't exist.")
        changes["share"] = s
    if body.get("display") in ("basic", "hd"):
        changes["display"] = body["display"]
    if "clipboard" in body:
        changes["clipboard"] = bool(body["clipboard"])
    core.update(name, **changes)
    return {"ok": True, "restart_needed": core.is_running(name) and bool(set(changes) - {"clipboard"})}


def route(method, parts, body):
    """parts: the path split on '/', after 'api'. Returns a JSON-able object."""
    if method == "GET" and parts == ["state"]:
        return full_state()
    if method == "POST" and parts == ["vms"]:
        return create_job(body)
    if method == "POST" and parts == ["pick-folder"]:
        return pick_folder()
    if method == "POST" and len(parts) == 3 and parts[0] == "jobs" and parts[2] == "dismiss":
        with JOBS_LOCK:
            JOBS.pop(parts[1], None)
        return {"ok": True}
    if len(parts) >= 3 and parts[0] == "vms":
        name, action = parts[1], parts[2]
        core.load(name)
        if method == "POST" and action == "start":
            runtime.start(name, bool(body.get("headless")))
            return {"ok": True}
        if method == "POST" and action == "stop":
            threading.Thread(target=core.stop, args=(name, bool(body.get("force"))), daemon=True).start()
            return {"ok": True}
        if method == "POST" and action == "delete":
            core.delete_vm(name)
            return {"ok": True}
        if method == "POST" and action == "run":
            return runtime.run_command(name, body.get("cmd", ""), body.get("shell", "cmd"), body.get("cwd"),
                                       int(body.get("timeout") or 120))
        if method == "POST" and action == "open":
            return runtime.open_in_windows(name, body.get("target", ""))
        if method == "POST" and action == "rdp":
            from .cli import write_rdp_file
            subprocess.run(["open", write_rdp_file(name)])
            return {"ok": True}
        if method == "POST" and action == "reveal":
            subprocess.run(["open", core.vm_dir(name)])
            return {"ok": True}
        if method == "POST" and action == "settings":
            return settings(name, body)
        if action == "snapshots":
            if method == "GET":
                return {"snapshots": core.snapshot(name, "list")}
            if method == "POST" and len(parts) == 3:
                core.snapshot(name, "save", body.get("tag"))
                return {"ok": True}
            if method == "POST" and len(parts) == 5 and parts[4] == "restore":
                core.snapshot(name, "restore", parts[3])
                return {"ok": True}
            if method == "DELETE" and len(parts) == 4:
                core.snapshot(name, "delete", parts[3])
                return {"ok": True}
    raise StrataError("Unknown request.")


class Handler(BaseHTTPRequestHandler):
    server_version = "Strata"

    def log_message(self, *args):
        pass

    def _send(self, status, payload, ctype="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        # Blocks DNS-rebinding: only accept requests addressed to our own loopback origin.
        return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _handle(self, method):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        url = urlparse(self.path)
        if url.path.startswith("/api/"):
            if not secrets.compare_digest(self.headers.get("X-Strata-Token", ""), TOKEN):
                return self._send(401, {"error": "Open the dashboard with: strata dashboard"})
            if url.path == "/api/ping":
                return self._send(200, {"ok": True})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                parts = [unquote(p) for p in url.path[5:].split("/") if p]
                return self._send(200, route(method, parts, body))
            except StrataError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                return self._send(500, {"error": f"Unexpected error: {e}"})
        if method != "GET":
            return self._send(405, {"error": "method not allowed"})
        rel = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
        path = os.path.realpath(os.path.join(WEB_DIR, rel))
        if not path.startswith(WEB_DIR + os.sep) or not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        with open(path, "rb") as f:
            self._send(200, f.read(), TYPES.get(os.path.splitext(path)[1], "application/octet-stream"))

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")


TOKEN = None


def serve():
    global TOKEN
    TOKEN = dashboard_token()
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    httpd.daemon_threads = True
    httpd.serve_forever()


def _alive():
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/ping",
                                     headers={"X-Strata-Token": dashboard_token()})
        with urllib.request.urlopen(req, timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def open_dashboard(foreground=False):
    if foreground:
        return serve()
    if not _alive():
        log = open(os.path.join(core.HOME, "dashboard.log"), "ab")
        subprocess.Popen([sys.executable, "-m", "strata", "dashboard", "--foreground"],
                         cwd=runtime.PKG_ROOT, env=dict(os.environ, PYTHONPATH=runtime.PKG_ROOT),
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        for _ in range(50):
            if _alive():
                break
            time.sleep(0.1)
        else:
            raise StrataError(f"The dashboard didn't start. See {os.path.join(core.HOME, 'dashboard.log')}")
    url = f"http://127.0.0.1:{PORT}/#t={dashboard_token()}"
    subprocess.run(["open", url])
    print(f"Dashboard: http://127.0.0.1:{PORT}  (opened in your browser)")
