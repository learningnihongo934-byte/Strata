"""The `strata` command."""

import argparse
import sys

from . import core, engine, games
from .core import StrataError

BOLD, DIM, GREEN, RED, RESET = ("\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m") \
    if sys.stdout.isatty() else ("",) * 5


def progress_bar(label, done, total):
    if not total:
        sys.stdout.write(f"\r{label}...\033[K")
    else:
        pct = done / total
        bar = "█" * int(pct * 30) + "░" * (30 - int(pct * 30))
        sys.stdout.write(f"\r{label}  {bar} {pct * 100:5.1f}%  {done / 1e6:.0f} / {total / 1e6:.0f} MB\033[K")
        if done >= total:
            sys.stdout.write("\n")
    sys.stdout.flush()


def cmd_setup(a):
    engine.install(progress_bar)
    print(f"\n{GREEN}Strata is set up.{RESET} Install a store with: strata store install steam")


def cmd_store(a):
    if a.action == "list" or not a.action:
        for k, s in games.STORES.items():
            state = f"{GREEN}installed{RESET}" if engine.installed() and games.store_installed(k) else f"{DIM}not installed{RESET}"
            print(f"  {k:<6} {s['name']:<12} {state}   {DIM}{s['about']}{RESET}")
        return
    if a.store not in games.STORES:
        raise StrataError(f"Pick one of: {', '.join(games.STORES)}")
    if a.action == "install":
        games.install_store(a.store, progress_bar)
        print(f"\n{GREEN}{games.STORES[a.store]['name']} installed.{RESET} Open it with: strata store open {a.store}")
    else:
        games.open_store(a.store)
        print(f"Opening {games.STORES[a.store]['name']}...")


def cmd_games(a):
    lib = games.library()
    if not lib:
        print("No games yet. Install one through a store (strata store open steam) or add an .exe: strata add <file>")
    for g in lib:
        on = [k for k, v in g["settings"].items() if v and not engine.SETTINGS[k]["default"]]
        print(f"  {g['name']:<40} {DIM}{g['source']:<7} {g['id']}{RESET}" + (f"  [{', '.join(on)}]" if on else ""))


def cmd_play(a):
    g = games.find(" ".join(a.game))
    games.play(g)
    print(f"Starting {g['name']}...")


def cmd_add(a):
    gid = games.add_game(a.path, a.name)
    print(f"{GREEN}Added{RESET} ({gid}). Play it with: strata play {gid}")


def cmd_remove(a):
    g = games.find(" ".join(a.game))
    games.remove_game(g["id"])
    print(f"Removed {g['name']} from your library.")


def cmd_run(a):
    games.run_exe(a.path)
    print("Running it in Windows. After installing a game, add its .exe with: strata add <path>")


def cmd_settings(a):
    g = games.find(a.game)
    changes = {k: v == "on" for k, v in (pair.split("=", 1) for pair in a.changes)} if a.changes else {}
    bad = set(changes) - set(engine.SETTINGS)
    if bad:
        raise StrataError(f"Unknown setting(s): {', '.join(bad)}. Choose from: {', '.join(engine.SETTINGS)}")
    s = games.set_settings(g["id"], changes) if changes else g["settings"]
    print(f"{BOLD}{g['name']}{RESET}")
    for k, meta in engine.SETTINGS.items():
        print(f"  {k:<8} {GREEN + 'on ' if s[k] else DIM + 'off'}{RESET}  {meta['label']} {DIM}- {meta['help']}{RESET}")
    if not changes:
        print(f"\nChange with e.g.: strata settings \"{g['name']}\" hud=on retina=off")


def cmd_dock(a):
    g = games.find(" ".join(a.game))
    app = games.make_dock_app(g)
    print(f"{GREEN}Added {g['name']} to your Dock.{RESET} ({app})")


def cmd_stop(a):
    engine.stop_all()
    print("Closed all Windows games and stores.")


def cmd_doctor(a):
    ok_all = True
    for check, ok, hint in core.doctor():
        print(f"  {GREEN + '✓' if ok else RED + '✗'}{RESET} {check}" + ("" if ok else f"  {DIM}{hint}{RESET}"))
        ok_all &= ok
    print(f"\n{core.chip_name()}, {core.memory_gb()} GB memory, macOS {'.'.join(map(str, core.macos_version()))}")
    sys.exit(0 if ok_all else 1)


def cmd_dashboard(a):
    from . import server
    server.open_dashboard(foreground=a.foreground)


def main(argv=None):
    p = argparse.ArgumentParser(prog="strata", description="Play Windows games on your Mac, for free.")
    p.add_argument("--version", action="version", version=f"Strata {core.VERSION}")
    sub = p.add_subparsers(dest="cmd", metavar="command")

    def add(name, fn, help, aliases=()):
        sp = sub.add_parser(name, help=help, aliases=list(aliases))
        sp.set_defaults(fn=fn)
        return sp

    s = add("dashboard", cmd_dashboard, "open the Strata dashboard in your browser", ["ui"])
    s.add_argument("--foreground", action="store_true", help=argparse.SUPPRESS)
    add("setup", cmd_setup, "download the game engine and create the Windows environment")
    s = add("store", cmd_store, "list / install / open Steam, Epic Games, GOG Galaxy")
    s.add_argument("action", nargs="?", choices=["list", "install", "open"])
    s.add_argument("store", nargs="?")
    add("games", cmd_games, "list your games", ["ls"])
    s = add("play", cmd_play, "start a game (name or id)")
    s.add_argument("game", nargs="+")
    s = add("add", cmd_add, "add a Windows game .exe to your library")
    s.add_argument("path")
    s.add_argument("--name")
    s = add("remove", cmd_remove, "remove a game from your library")
    s.add_argument("game", nargs="+")
    s = add("run", cmd_run, "run any Windows program or installer (.exe / .msi)")
    s.add_argument("path")
    s = add("settings", cmd_settings, "show or change a game's settings, e.g. hud=on retina=off")
    s.add_argument("game")
    s.add_argument("changes", nargs="*")
    s = add("dock", cmd_dock, "make a Mac app for a game and add it to the Dock")
    s.add_argument("game", nargs="+")
    add("stop", cmd_stop, "close all running Windows games and stores")
    add("doctor", cmd_doctor, "check that your Mac is ready")

    a = p.parse_args(argv)
    if not getattr(a, "fn", None):
        p.print_help()
        print(f"\nNew here? Run {BOLD}strata dashboard{RESET} to do everything with buttons.")
        return
    try:
        a.fn(a)
    except StrataError as e:
        print(f"{RED}{e}{RESET}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
