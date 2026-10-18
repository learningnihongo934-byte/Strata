"""Paths, settings storage, downloads and host checks shared by every part of Strata."""

import json
import os
import platform
import subprocess
import time
import urllib.error
import urllib.request

VERSION = "2.0.0"

HOME = os.path.expanduser(os.environ.get("STRATA_HOME", "~/.strata"))
CACHE_DIR = os.path.join(HOME, "cache")
RUNTIME_DIR = os.path.join(HOME, "runtime")
BOTTLE = os.path.join(HOME, "bottle")          # the one Windows environment all games share
LOG_DIR = os.path.join(HOME, "logs")
LIBRARY_FILE = os.path.join(HOME, "library.json")
DOCK_APPS_DIR = os.path.expanduser("~/Applications/Strata Games")
PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Strata/" + VERSION


class StrataError(Exception):
    """An error with a message that is safe and helpful to show a beginner."""


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, indent=2)
    os.replace(path + ".tmp", path)


def macos_version():
    try:
        return tuple(int(x) for x in platform.mac_ver()[0].split(".")[:2])
    except ValueError:
        return (0, 0)


def rosetta_installed():
    return os.path.exists("/Library/Apple/usr/libexec/oah/libRosettaRuntime")


def chip_name():
    try:
        return subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
    except Exception:
        return "Unknown"


def memory_gb():
    try:
        return int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)) // 1024 ** 3
    except Exception:
        return 0


def doctor():
    """Returns [(check, ok, hint)] for the host. Rosetta and the engine can be fixed from the dashboard."""
    from . import engine
    return [
        ("Apple Silicon Mac", os.uname().machine == "arm64", "Strata needs an M-series Mac."),
        ("macOS 14 Sonoma or newer", macos_version() >= (14, 0), "Update macOS in System Settings → General."),
        ("Rosetta 2", rosetta_installed(), "Run: softwareupdate --install-rosetta --agree-to-license"),
        ("Game engine (Wine + D3DMetal)", engine.installed(), "Click 'Set up Strata' in the dashboard, or run: strata setup"),
    ]


def download(url, dest, progress=None, label="Downloading"):
    """Downloads with resume support. progress(label, done_bytes, total_bytes) is called as it goes."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"User-Agent": UA}
    if have:
        headers["Range"] = f"bytes={have}-"
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:
            os.replace(part, dest)
            return dest
        raise StrataError(f"Download failed ({url.split('/')[2]} answered HTTP {e.code}).")
    except Exception as e:
        raise StrataError(f"Download failed: {e}. Check your internet connection.")
    with r:
        if have and r.status != 206:
            have = 0
        total = have + int(r.headers.get("Content-Length") or 0)
        done, last = have, 0
        with open(part, "ab" if have else "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if progress and time.time() - last > 0.4:
                    progress(label, done, total)
                    last = time.time()
    if total and done < total:
        raise StrataError("The download was interrupted. Try again; it resumes where it stopped.")
    os.replace(part, dest)
    if progress:
        progress(label, done, done)
    return dest
