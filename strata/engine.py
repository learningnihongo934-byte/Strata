"""The game engine: Apple's Game Porting Toolkit (Wine + D3DMetal), downloaded from Gcenx's free builds."""

import os
import shutil
import subprocess
import tarfile

from . import core
from .core import StrataError

GPTK_URL = ("https://github.com/Gcenx/game-porting-toolkit/releases/download/"
            "Game-Porting-Toolkit-3.0-3/game-porting-toolkit-3.0-3.tar.xz")
WINE_DIR = os.path.join(core.RUNTIME_DIR, "wine")
WINE = os.path.join(WINE_DIR, "bin", "wine64")
WINESERVER = os.path.join(WINE_DIR, "bin", "wineserver")

# Per-game switches. "env" values are set when the switch is on.
SETTINGS = {
    "esync":   {"label": "Faster CPU sync (ESync)", "default": True,  "env": {"WINEESYNC": "1"},
                "help": "Usually more FPS. Turn off if a game freezes."},
    "avx":     {"label": "AVX support", "default": True, "env": {"ROSETTA_ADVERTISE_AVX": "1"},
                "help": "Needed by many newer games. Turn off if a game crashes at start."},
    "retina":  {"label": "Retina (sharper)", "default": False,
                "help": "Full Mac resolution. Looks better, costs FPS."},
    "hud":     {"label": "FPS overlay", "default": False, "env": {"MTL_HUD_ENABLED": "1"},
                "help": "Apple's Metal HUD: FPS, frame time and GPU use."},
    "metalfx": {"label": "MetalFX upscaling", "default": False, "env": {"D3DM_ENABLE_METALFX": "1"},
                "help": "Replaces DLSS with Apple's MetalFX in games that support DLSS."},
    "dxr":     {"label": "Ray tracing", "default": False, "env": {"D3DM_SUPPORT_DXR": "1"},
                "help": "DirectX ray tracing (M3 or newer). Heavy on performance."},
}


def default_settings():
    return {k: v["default"] for k, v in SETTINGS.items()}


def installed():
    return os.path.exists(WINE) and os.path.exists(os.path.join(core.BOTTLE, "system.reg"))


def env(settings=None):
    e = dict(os.environ, WINEPREFIX=core.BOTTLE, WINEDEBUG="-all")
    s = {**default_settings(), **(settings or {})}
    for key, on in s.items():
        if on and key in SETTINGS:
            e.update(SETTINGS[key].get("env", {}))
    return e


def install(progress=None):
    """Downloads the engine (~240 MB, once) and creates the shared Windows environment."""
    if not rosetta_ok():
        raise StrataError("Rosetta 2 is needed. Run: softwareupdate --install-rosetta --agree-to-license")
    if not os.path.exists(WINE):
        archive = core.download(GPTK_URL, os.path.join(core.CACHE_DIR, "gptk.tar.xz"), progress,
                                "Downloading the game engine")
        if progress:
            progress("Unpacking the game engine", 0, 0)
        tmp = os.path.join(core.RUNTIME_DIR, "tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        with tarfile.open(archive) as t:
            t.extractall(tmp, filter="data")
        src = os.path.join(tmp, "Game Porting Toolkit.app", "Contents", "Resources", "wine")
        shutil.rmtree(WINE_DIR, ignore_errors=True)
        shutil.move(src, WINE_DIR)
        shutil.rmtree(tmp, ignore_errors=True)
        os.remove(archive)
    if not os.path.exists(os.path.join(core.BOTTLE, "system.reg")):
        if progress:
            progress("Creating the Windows environment (takes a few minutes the first time)", 0, 0)
        e = env()
        e["WINEDLLOVERRIDES"] = "mscoree,mshtml="  # skip the Mono/Gecko install prompts
        r = subprocess.run([WINE, "wineboot", "--init"], env=e, capture_output=True, text=True)
        subprocess.run([WINESERVER, "-w"], env=e)
        if r.returncode != 0 or not os.path.exists(os.path.join(core.BOTTLE, "system.reg")):
            raise StrataError("Couldn't create the Windows environment:\n" + (r.stderr or "")[-600:])
        reg("HKCU\\Software\\Wine", "Version", "win10")


def rosetta_ok():
    return core.rosetta_installed()


def reg(key, name, value):
    subprocess.run([WINE, "reg", "add", key, "/v", name, "/d", value, "/f"], env=env(), capture_output=True)


def require():
    if not installed():
        raise StrataError("Strata isn't set up yet. Run: strata setup  (or click 'Set up Strata' in the dashboard)")


def win_to_unix(path):
    """C:\\Games\\x.exe -> ~/.strata/bottle/drive_c/Games/x.exe"""
    if len(path) > 2 and path[1] == ":" and path[0].lower() == "c":
        return os.path.join(core.BOTTLE, "drive_c", *path[3:].replace("\\", "/").split("/"))
    return path


def launch(target, args=(), settings=None, log_name="wine"):
    """Starts a Windows program (unix path, C:\\ path or URL) in the background."""
    require()
    s = {**default_settings(), **(settings or {})}
    reg("HKCU\\Software\\Wine\\Mac Driver", "RetinaMode", "y" if s.get("retina") else "n")
    unix = win_to_unix(target)
    cwd = os.path.dirname(unix) if os.path.exists(unix) else core.BOTTLE
    if os.path.exists(unix) and unix.lower().endswith(".msi"):
        cmd = [WINE, "msiexec", "/i", unix, *args]
    elif os.path.exists(unix):
        cmd = [WINE, unix, *args]
    else:
        cmd = [WINE, "start", target, *args]  # URLs such as com.epicgames.launcher://...
    os.makedirs(core.LOG_DIR, exist_ok=True)
    log = open(os.path.join(core.LOG_DIR, f"{log_name}.log"), "ab")
    return subprocess.Popen(cmd, cwd=cwd, env=env(s), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                            start_new_session=True)


def run_wait(target, args=(), timeout=None):
    """Runs a Windows program and waits (used for silent installers)."""
    require()
    unix = win_to_unix(target)
    cmd = [WINE, "msiexec", "/i", unix, *args] if unix.lower().endswith(".msi") else [WINE, unix, *args]
    return subprocess.run(cmd, env=env(), capture_output=True, timeout=timeout).returncode


def stop_all():
    if os.path.exists(WINESERVER):
        subprocess.run([WINESERVER, "-k"], env=env(), capture_output=True)


def running():
    r = subprocess.run(["pgrep", "-f", core.BOTTLE], capture_output=True)
    return r.returncode == 0 or subprocess.run(["pgrep", "-x", "wineserver"], capture_output=True).returncode == 0
