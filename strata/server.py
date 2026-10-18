"""The local web dashboard: a small JSON API plus static files, bound to 127.0.0.1 only."""

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from . import core, engine, games
from .core import StrataError

PORT = int(os.environ.get("STRATA_PORT", "7650"))
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
TOKEN_FILE = os.path.join(core.HOME, "dashboard.token")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg"}
JOBS = {}  # key -> {"label", "stage", "done", "total", "error"}
LOCK = threading.Lock()
TOKEN = None


def dashboard_token():
    os.makedirs(core.HOME, exist_ok=True)
    if not os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "w") as f:
            f.write(secrets.token_hex(16))
        os.chmod(TOKEN_FILE, 0o600)
    with open(TOKEN_FILE) as f:
        return f.read().strip()


def start_job(key, label, fn):
    with LOCK:
        if key in JOBS and not JOBS[key].get("error"):
            raise StrataError(f"{label} is already in progress.")
        JOBS[key] = {"label": label, "stage": "Starting", "done": 0, "total": 0, "error": None}

    def progress(stage, done, total):
        with LOCK:
            JOBS[key].update(stage=stage, done=done, total=total)

    def work():
        try:
            fn(progress)
            with LOCK:
                JOBS.pop(key, None)
        except Exception as e:
            with LOCK:
                JOBS[key]["error"] = str(e)

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True}


def state():
    ready = engine.installed()
    lib = games.library() if ready else []
    for g in lib:
        g["cover"] = (f"/cover/{g['id']}" if games.cover_path(g) else games.cover_url(g))
    with LOCK:
        jobs = {k: dict(v) for k, v in JOBS.items()}
    return {
        "version": core.VERSION, "ready": ready, "jobs": jobs, "games": lib,
        "stores": [{"key": k, "name": s["name"], "about": s["about"],
                    "installed": ready and games.store_installed(k)} for k, s in games.STORES.items()],
        "settings": {k: {"label": v["label"], "help": v["help"]} for k, v in engine.SETTINGS.items()},
        "doctor": [{"check": c, "ok": ok, "hint": h} for c, ok, h in core.doctor()],
        "mac": {"chip": core.chip_name(), "memory_gb": core.memory_gb()},
    }


def pick_file(kind):
    prompt = "Choose the game's .exe file" if kind == "game" else "Choose a Windows program or installer"
    start = os.path.join(core.BOTTLE, "drive_c") if kind == "game" else os.path.expanduser("~/Downloads")
    r = subprocess.run(["osascript", "-e", f'POSIX path of (choose file with prompt "{prompt}" '
                        f'default location (POSIX file "{start}"))'], capture_output=True, text=True)
    return r.stdout.strip() or None


def route(method, parts, body):
    if method == "GET" and parts == ["state"]:
        return state()
    if method == "POST" and parts == ["setup"]:
        return start_job("setup", "Setting up Strata", engine.install)
    if method == "POST" and len(parts) == 3 and parts[0] == "stores":
        key, action = parts[1], parts[2]
        if key not in games.STORES:
            raise StrataError("Unknown store.")
        if action == "install":
            return start_job(key, f"Installing {games.STORES[key]['name']}", lambda p: games.install_store(key, p))
        if action == "open":
            games.open_store(key)
            return {"ok": True}
    if method == "POST" and len(parts) == 3 and parts[0] == "jobs" and parts[2] == "dismiss":
        with LOCK:
            JOBS.pop(parts[1], None)
        return {"ok": True}
    if method == "POST" and parts == ["add"]:
        path = body.get("path") or pick_file("game")
        return {"id": games.add_game(path, body.get("name"))} if path else {"cancelled": True}
    if method == "POST" and parts == ["run"]:
        path = pick_file("installer")
        if path:
            games.run_exe(path)
        return {"ok": bool(path)}
    if method == "POST" and parts == ["stop"]:
        engine.stop_all()
        return {"ok": True}
    if method == "POST" and parts == ["open-folder"]:
        subprocess.run(["open", os.path.join(core.BOTTLE, "drive_c")])
        return {"ok": True}
    if len(parts) == 3 and parts[0] == "games":
        g = games.find(parts[1])
        action = parts[2]
        if method == "POST" and action == "play":
            games.play(g)
            return {"ok": True}
        if method == "POST" and action == "settings":
            return {"settings": games.set_settings(g["id"], body)}
        if method == "POST" and action == "dock":
            games.make_dock_app(g)
            return {"ok": True}
        if method == "POST" and action == "remove":
            games.remove_game(g["id"])
            return {"ok": True}
    raise StrataError("Unknown request.")


class Handler(BaseHTTPRequestHandler):
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

    def _handle(self, method):
        # Only our own loopback origin (blocks DNS rebinding).
        if self.headers.get("Host", "") not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
            return self._send(403, {"error": "forbidden"})
        url = urlparse(self.path)
        if url.path.startswith("/api/"):
            if not secrets.compare_digest(self.headers.get("X-Strata-Token", ""), TOKEN):
                return self._send(401, {"error": "Open the dashboard with: strata dashboard"})
            if url.path == "/api/ping":
                return self._send(200, {"ok": True})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length)) if length else {}
                parts = [unquote(p) for p in url.path[5:].split("/") if p]
                return self._send(200, route(method, parts, body))
            except StrataError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                return self._send(500, {"error": f"Unexpected error: {e}"})
        if method != "GET":
            return self._send(405, {"error": "method not allowed"})
        if url.path.startswith("/cover/"):
            try:
                path = games.cover_path(games.find(unquote(url.path[7:])))
            except StrataError:
                path = None
            if not path:
                return self._send(404, {"error": "no cover"})
            with open(path, "rb") as f:
                return self._send(200, f.read(), "image/jpeg")
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


def serve():
    global TOKEN
    TOKEN = dashboard_token()
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    httpd.daemon_threads = True
    httpd.serve_forever()


def _alive():
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/ping", headers={"X-Strata-Token": dashboard_token()})
        with urllib.request.urlopen(req, timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def open_dashboard(foreground=False):
    if foreground:
        return serve()
    if not _alive():
        log = open(os.path.join(core.HOME, "dashboard.log"), "ab")
        subprocess.Popen([sys.executable, "-m", "strata", "dashboard", "--foreground"], cwd=core.PKG_ROOT,
                         env=dict(os.environ, PYTHONPATH=core.PKG_ROOT), stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True)
        for _ in range(50):
            if _alive():
                break
            time.sleep(0.1)
        else:
            raise StrataError(f"The dashboard didn't start. See {os.path.join(core.HOME, 'dashboard.log')}")
    subprocess.run(["open", f"http://127.0.0.1:{PORT}/#t={dashboard_token()}"])
    print(f"Dashboard: http://127.0.0.1:{PORT}  (opened in your browser)")
