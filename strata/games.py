"""Game stores (Steam, Epic, GOG), the game library, and Mac Dock apps for games."""

import glob
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import tempfile

from . import core, engine
from .core import StrataError

C = "C:\\Program Files (x86)\\"
STORES = {
    "steam": {
        "name": "Steam", "installer": "https://cdn.akamai.steamstatic.com/client/installer/SteamSetup.exe",
        "file": "SteamSetup.exe", "silent": ["/S"], "exe": C + "Steam\\steam.exe",
        "args": ["-cef-disable-gpu", "-cef-disable-sandbox"],
        "about": "The biggest PC game store. Most single-player games work well.",
    },
    "epic": {
        "name": "Epic Games",
        "installer": "https://launcher-public-service-prod06.ol.epicgames.com/launcher/api/installer/download/EpicGamesLauncherInstaller.msi",
        "file": "EpicGamesLauncherInstaller.msi", "silent": ["/qn"],
        "exe": C + "Epic Games\\Launcher\\Portal\\Binaries\\Win64\\EpicGamesLauncher.exe",
        "args": ["-opengl", "-SkipBuildPatchPrereq"],
        "about": "Free games every week. Games with anti-cheat won't run.",
    },
    "gog": {
        "name": "GOG Galaxy", "installer": "https://webinstallers.gog-statics.com/download/GOG_Galaxy_2.0.exe",
        "file": "GOG_Galaxy_2.0.exe", "silent": None, "exe": C + "GOG Galaxy\\GalaxyClient.exe", "args": [],
        "about": "DRM-free games. The installer opens; click through it once.",
    },
}


# ---------------------------------------------------------------- stores

def store_installed(key):
    return os.path.exists(engine.win_to_unix(STORES[key]["exe"]))


def install_store(key, progress=None):
    engine.require()
    s = STORES[key]
    path = core.download(s["installer"], os.path.join(core.CACHE_DIR, s["file"]), progress, f"Downloading {s['name']}")
    if progress:
        progress(f"Installing {s['name']}" + ("" if s["silent"] else " (finish the installer window)"), 0, 0)
    if s["silent"] is None:
        engine.launch(path, log_name=key).wait()
    else:
        engine.run_wait(path, s["silent"], timeout=1800)
    if not store_installed(key):
        raise StrataError(f"{s['name']} didn't finish installing. Try again, or see ~/.strata/logs.")
    os.remove(path)


def open_store(key):
    if not store_installed(key):
        raise StrataError(f"{STORES[key]['name']} isn't installed yet.")
    engine.launch(STORES[key]["exe"], STORES[key]["args"], log_name=key)


# ---------------------------------------------------------------- library

def _drive_c(*parts):
    return os.path.join(core.BOTTLE, "drive_c", *parts)


def _acf_value(text, key):
    m = re.search(rf'"{key}"\s+"([^"]*)"', text)
    return m.group(1) if m else None


def detect_steam():
    games = []
    skip = re.compile(r"redistributable|steamworks|proton|runtime", re.I)
    for acf in glob.glob(_drive_c("Program Files (x86)", "Steam", "steamapps", "appmanifest_*.acf")):
        text = open(acf, errors="replace").read()
        appid, name = _acf_value(text, "appid"), _acf_value(text, "name")
        if appid and name and not skip.search(name) and _acf_value(text, "StateFlags") == "4":
            games.append({"id": f"steam-{appid}", "name": name, "source": "steam", "appid": appid})
    return games


def detect_epic():
    games = []
    for item in glob.glob(_drive_c("ProgramData", "Epic", "EpicGamesLauncher", "Data", "Manifests", "*.item")):
        d = core.read_json(item, {})
        if d.get("AppName") and d.get("DisplayName") and not d.get("bIsIncompleteInstall"):
            games.append({"id": f"epic-{d['AppName']}", "name": d["DisplayName"], "source": "epic",
                          "app": d["AppName"]})
    return games


def detect_gog():
    games = []
    for info in glob.glob(_drive_c("Program Files (x86)", "GOG Galaxy", "Games", "*", "goggame-*.info")):
        d = core.read_json(info, {})
        task = next((t for t in d.get("playTasks", []) if t.get("isPrimary")), None)
        if d.get("name") and task and task.get("path"):
            exe = os.path.join(os.path.dirname(info), task["path"].replace("\\", "/"))
            games.append({"id": f"gog-{d.get('gameId')}", "name": d["name"], "source": "gog", "exe": exe,
                          "args": task.get("arguments", "").split()})
    return games


def library():
    """Detected store games + games you added, each with merged settings."""
    data = core.read_json(core.LIBRARY_FILE, {"custom": [], "settings": {}, "hidden": []})
    games = (detect_steam() + detect_epic() + detect_gog()) if engine.installed() else []
    games += data["custom"]
    out = []
    for g in games:
        if g["id"] in data["hidden"]:
            continue
        g["settings"] = {**engine.default_settings(), **data["settings"].get(g["id"], {})}
        g["dock"] = os.path.exists(dock_app_path(g))
        out.append(g)
    return sorted(out, key=lambda g: g["name"].lower())


def find(query):
    games = library()
    for g in games:
        if g["id"] == query:
            return g
    hits = [g for g in games if query.lower() in g["name"].lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise StrataError(f"No game matching '{query}'. See your games with: strata games")
    raise StrataError(f"'{query}' matches several games: {', '.join(g['name'] for g in hits)}")


def add_game(path, name=None):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path) or not path.lower().endswith(".exe"):
        raise StrataError("Pick a Windows game file ending in .exe.")
    data = core.read_json(core.LIBRARY_FILE, {"custom": [], "settings": {}, "hidden": []})
    gid = "custom-" + hashlib.sha1(path.encode()).hexdigest()[:10]
    data["custom"] = [g for g in data["custom"] if g["id"] != gid]
    name = name or os.path.splitext(os.path.basename(path))[0]
    data["custom"].append({"id": gid, "name": name, "source": "custom", "exe": path, "args": []})
    if gid in data["hidden"]:
        data["hidden"].remove(gid)
    core.write_json(core.LIBRARY_FILE, data)
    return gid


def remove_game(gid):
    data = core.read_json(core.LIBRARY_FILE, {"custom": [], "settings": {}, "hidden": []})
    if any(g["id"] == gid for g in data["custom"]):
        data["custom"] = [g for g in data["custom"] if g["id"] != gid]
    elif gid not in data["hidden"]:
        data["hidden"].append(gid)  # store games are hidden, not uninstalled
    core.write_json(core.LIBRARY_FILE, data)


def set_settings(gid, changes):
    data = core.read_json(core.LIBRARY_FILE, {"custom": [], "settings": {}, "hidden": []})
    cur = data["settings"].get(gid, {})
    cur.update({k: bool(v) for k, v in changes.items() if k in engine.SETTINGS})
    data["settings"][gid] = cur
    core.write_json(core.LIBRARY_FILE, data)
    return {**engine.default_settings(), **cur}


def play(game):
    s = game["settings"]
    if game["source"] == "steam":
        if not store_installed("steam"):
            raise StrataError("Steam isn't installed.")
        return engine.launch(STORES["steam"]["exe"], [*STORES["steam"]["args"], "-applaunch", game["appid"]], s, "steam")
    if game["source"] == "epic":
        return engine.launch(f"com.epicgames.launcher://apps/{game['app']}?action=launch&silent=true", (), s, "epic")
    return engine.launch(game["exe"], game.get("args", []), s, game["id"])


def run_exe(path):
    """Runs any Windows program or installer once (e.g. setup.exe for a game you downloaded)."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path) or not path.lower().endswith((".exe", ".msi")):
        raise StrataError("Pick a Windows program (.exe or .msi).")
    engine.launch(path, log_name="run")


# ---------------------------------------------------------------- covers & Dock apps

def cover_path(game):
    if game["source"] == "steam":
        base = _drive_c("Program Files (x86)", "Steam", "appcache", "librarycache")
        for pattern in (f"{game['appid']}/header.jpg", f"{game['appid']}_header.jpg", f"{game['appid']}/*.jpg"):
            hits = glob.glob(os.path.join(base, pattern))
            if hits:
                return hits[0]
    return None


def cover_url(game):
    if game["source"] == "steam":
        return f"https://cdn.cloudflare.steamstatic.com/steam/apps/{game['appid']}/header.jpg"
    return None


def _safe_name(name):
    return re.sub(r'[/:\\*?"<>|]', "", name).strip() or "Game"


def dock_app_path(game):
    return os.path.join(core.DOCK_APPS_DIR, _safe_name(game["name"]) + ".app")


def _make_icns(image, dest):
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "icon.iconset")
        os.makedirs(iconset)
        square = os.path.join(tmp, "square.png")
        subprocess.run(["sips", "-s", "format", "png", "-c", "512", "512", image, "--out", square], capture_output=True)
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                subprocess.run(["sips", "-z", str(px), str(px), square, "--out",
                                os.path.join(iconset, f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png")],
                               capture_output=True)
        return subprocess.run(["iconutil", "-c", "icns", iconset, "-o", dest], capture_output=True).returncode == 0


def make_dock_app(game, add_to_dock=True):
    """Creates a small Mac app that launches the game, and pins it to the Dock."""
    app = dock_app_path(game)
    shutil.rmtree(app, ignore_errors=True)
    macos = os.path.join(app, "Contents", "MacOS")
    res = os.path.join(app, "Contents", "Resources")
    os.makedirs(macos)
    os.makedirs(res)
    script = os.path.join(macos, "launch")
    strata = os.path.join(core.PKG_ROOT, "bin", "strata")
    with open(script, "w") as f:
        f.write(f"#!/bin/bash\nexec {json.dumps(strata)} play {json.dumps(game['id'])}\n")
    os.chmod(script, 0o755)
    plist = {"CFBundleName": game["name"], "CFBundleDisplayName": game["name"], "CFBundleExecutable": "launch",
             "CFBundleIdentifier": "com.strata.game." + re.sub(r"[^A-Za-z0-9]", "", game["id"]),
             "CFBundlePackageType": "APPL", "CFBundleIconFile": "icon", "LSUIElement": True}
    img = cover_path(game)
    if not img and cover_url(game):
        img = os.path.join(core.CACHE_DIR, f"{game['id']}.jpg")
        try:
            core.download(cover_url(game), img)
        except StrataError:
            img = None
    if not (img and _make_icns(img, os.path.join(res, "icon.icns"))):
        _make_icns(os.path.join(core.PKG_ROOT, "strata", "web", "icon.png"), os.path.join(res, "icon.icns"))
    with open(os.path.join(app, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump(plist, f)
    if add_to_dock:
        tile = ("<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key>"
                f"<string>file://{app.replace(' ', '%20')}/</string><key>_CFURLStringType</key><integer>15</integer>"
                "</dict></dict></dict>")
        subprocess.run(["defaults", "write", "com.apple.dock", "persistent-apps", "-array-add", tile])
        subprocess.run(["killall", "Dock"])
    return app
