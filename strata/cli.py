"""The `strata` command."""

import argparse
import os
import shutil
import subprocess
import sys
import time

from . import core, runtime, windows
from .core import StrataError

BOLD, DIM, GREEN, RED, CYAN, RESET = ("\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[36m", "\033[0m") \
    if sys.stdout.isatty() else ("",) * 6


def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def progress_bar(label, done, total):
    if not total:
        sys.stdout.write(f"\r{label}...\033[K")
    else:
        pct = done / total
        bar = "█" * int(pct * 30) + "░" * (30 - int(pct * 30))
        sys.stdout.write(f"\r{label}  {bar} {pct * 100:5.1f}%  {human_bytes(done)} / {human_bytes(total)}\033[K")
    sys.stdout.flush()
    if total and done >= total:
        sys.stdout.write("\n")


def pick_name(name):
    if name:
        return name
    names = core.list_names()
    if len(names) == 1:
        return names[0]
    if not names:
        raise StrataError("You don't have a VM yet. Create one with: strata create")
    raise StrataError(f"Which VM? You have: {', '.join(names)}")


def state_label(name):
    if not core.is_running(name):
        return f"{DIM}stopped{RESET}"
    st = core.runtime_status(name)
    if st.get("agent"):
        return f"{GREEN}running{RESET}"
    if not core.load(name).get("installed"):
        return f"{CYAN}installing Windows{RESET}"
    return f"{CYAN}starting{RESET}"


# ---------------------------------------------------------------- commands

def cmd_create(a):
    name = a.name or "windows"
    cfg = core.new_config(name, a.cpus, a.memory, a.disk, os.path.abspath(os.path.expanduser(a.share)) if a.share else None,
                          a.user, a.password)
    print(f"{BOLD}Creating '{name}'{RESET}: Windows 11 Pro for Arm, {cfg['cpus']} CPUs, "
          f"{cfg['memory_gb']} GB memory, {cfg['disk_gb']} GB disk")
    windows.create_vm(cfg, progress_bar)
    print(f"\n{GREEN}Ready!{RESET} Start it with: {BOLD}strata start {name}{RESET}")
    print("Windows installs itself on the first start (about 15-30 minutes, no clicks needed).")


def cmd_start(a):
    name = pick_name(a.name)
    if not runtime.start(name, a.headless):
        print(f"'{name}' is already running.")
        return
    cfg = core.load(name)
    print(f"{GREEN}Started '{name}'.{RESET}")
    if not cfg.get("installed"):
        print("Windows is installing itself. It will restart a few times; leave the window open.")


def cmd_stop(a):
    name = pick_name(a.name)
    if not core.is_running(name):
        print(f"'{name}' isn't running.")
        return
    print("Shutting down Windows..." if not a.force else "Turning off...")
    if core.stop(name, force=a.force):
        print(f"{GREEN}Stopped.{RESET}")
    else:
        print("Windows hasn't shut down yet (it may be installing updates). "
              f"To switch it off immediately: strata stop {name} --force")


def cmd_list(a):
    names = core.list_names()
    if not names:
        print("No VMs yet. Create one with: strata create")
        return
    print(f"{BOLD}{'NAME':<16}{'STATE':<30}{'CPUS':<6}{'MEMORY':<8}{'DISK USED'}{RESET}")
    for n in names:
        c = core.load(n)
        state = state_label(n)
        pad = 30 + (len(state) - len(state.replace(GREEN, "").replace(CYAN, "").replace(DIM, "").replace(RESET, "")))
        print(f"{n:<16}{state:<{pad}}{c['cpus']:<6}{str(c['memory_gb']) + ' GB':<8}{human_bytes(core.disk_usage_bytes(n))}")


def cmd_info(a):
    name = pick_name(a.name)
    c = core.load(name)
    print(f"{BOLD}{name}{RESET}  ({state_label(name)})")
    print(f"  System       Windows 11 Pro for Arm")
    print(f"  CPUs         {c['cpus']}")
    print(f"  Memory       {c['memory_gb']} GB")
    print(f"  Disk         {c['disk_gb']} GB max, {human_bytes(core.disk_usage_bytes(name))} used")
    print(f"  Display      {'High resolution (virtio-gpu)' if c.get('display') == 'hd' else 'Basic'}")
    print(f"  Shared       {c.get('share') or 'none'}{'  -> Z: in Windows' if c.get('share') else ''}")
    print(f"  Clipboard    {'synced' if c.get('clipboard', True) else 'off'}")
    print(f"  Windows user {c['username']}")
    print(f"  Files        {core.vm_dir(name)}")
    if core.is_running(name) and core.runtime_status(name).get("agent"):
        try:
            i = runtime.agent(c, "GET", "/info")
            print(f"  Uptime       {i['uptime_sec'] // 60} min, {i['free_mem_mb']} MB free, "
                  f"{i['disk_free_gb']} GB free on C:")
        except StrataError:
            pass


def cmd_delete(a):
    name = a.name
    core.load(name)
    if not a.yes:
        if input(f"Delete '{name}' and everything inside it? This can't be undone. Type the name to confirm: ") != name:
            print("Cancelled.")
            return
    core.delete_vm(name)
    print(f"Deleted '{name}'.")


def cmd_run(a):
    name = pick_name(a.vm)
    command = " ".join(a.command)
    if not command:
        raise StrataError('Give a command, e.g.: strata run windows ipconfig')
    r = runtime.run_command(name, command, "powershell" if a.ps else "cmd", timeout=a.timeout)
    sys.stdout.write(r.get("stdout") or "")
    sys.stderr.write(r.get("stderr") or "")
    if r.get("timed_out"):
        sys.stderr.write(f"\n(stopped after {a.timeout} seconds)\n")
    sys.exit(r.get("code") or 0)


def cmd_shell(a):
    name = pick_name(a.name)
    shell = "powershell" if a.ps else "cmd"
    cwd = ""
    print(f"{BOLD}Windows {('PowerShell' if a.ps else 'Command Prompt')} on '{name}'{RESET}  "
          f"{DIM}(type 'exit' to leave){RESET}")
    while True:
        try:
            line = input(f"{CYAN}{cwd or 'C:'}{RESET}{'> ' if not a.ps else ' PS> '}")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if line.strip() in ("exit", "quit"):
            return
        if not line.strip():
            continue
        try:
            r = runtime.run_command(name, line, shell, cwd)
        except StrataError as e:
            print(f"{RED}{e}{RESET}")
            continue
        sys.stdout.write(r.get("stdout") or "")
        sys.stdout.write(r.get("stderr") or "")
        cwd = r.get("cwd") or cwd


def cmd_open(a):
    runtime.open_in_windows(pick_name(a.vm), a.target)
    print(f"Opened {a.target} in Windows.")


def cmd_snapshot(a):
    name = pick_name(a.name)
    if a.action == "list" or not a.action:
        snaps = core.snapshot(name, "list")
        if not snaps:
            print(f"No snapshots. Save one with: strata snapshot {name} save before-update")
        for s in snaps:
            print(f"  {s['tag']:<28} {s['date']}  {s['size']}")
        return
    if not a.tag:
        raise StrataError(f"Give the snapshot a name, e.g.: strata snapshot {name} {a.action} before-update")
    if a.action == "restore" and core.is_running(name):
        print("Restoring the running VM to that moment...")
    elif a.action == "save" and core.is_running(name):
        print("Saving the running VM (memory too, so it resumes exactly where it was)...")
    core.snapshot(name, a.action, a.tag)
    print(f"{GREEN}Done.{RESET}")


def cmd_set(a):
    name = pick_name(a.name)
    changes = {}
    if a.cpus:
        changes["cpus"] = max(1, min(a.cpus, core.host_cpus() + 4))
    if a.memory:
        if a.memory >= core.host_memory_gb():
            raise StrataError(f"Your Mac has {core.host_memory_gb()} GB; give Windows less than that.")
        changes["memory_gb"] = a.memory
    if a.share is not None:
        changes["share"] = os.path.abspath(os.path.expanduser(a.share)) if a.share not in ("", "off", "none") else None
        if changes["share"] and not os.path.isdir(changes["share"]):
            raise StrataError("That folder doesn't exist.")
    if a.display:
        changes["display"] = a.display
    if a.clipboard:
        changes["clipboard"] = a.clipboard == "on"
    if not changes:
        raise StrataError("Nothing to change. Example: strata set windows --memory 8 --share ~/Documents")
    core.update(name, **changes)
    print(f"{GREEN}Saved.{RESET}" + (" Restart the VM for this to take effect." if core.is_running(name)
                                     and set(changes) - {"clipboard"} else ""))


def write_rdp_file(name):
    c = core.load(name)
    path = core.vm_path(name, f"{name}.rdp")
    with open(path, "w") as f:
        f.write(f"full address:s:127.0.0.1:{c['rdp_port']}\nusername:s:{c['username']}\n"
                "screen mode id:i:2\ndynamic resolution:i:1\nredirectclipboard:i:1\nprompt for credentials:i:0\n")
    return path


def cmd_rdp(a):
    name = pick_name(a.name)
    subprocess.run(["open", write_rdp_file(name)])
    print("Opening in Microsoft's free 'Windows App' (get it from the Mac App Store if nothing opens).")


def cmd_download(a):
    windows.ensure_windows_iso(progress_bar)
    windows.ensure_virtio(progress_bar)
    print(f"{GREEN}Windows 11 and drivers are downloaded.{RESET}")


def cmd_import_iso(a):
    windows.import_iso(a.path)
    print(f"{GREEN}Imported.{RESET} Now run: strata create")


def cmd_doctor(a):
    all_ok = True
    for check, ok, hint in core.doctor():
        print(f"  {GREEN + '✓' if ok else RED + '✗'}{RESET} {check}" + ("" if ok else f"  {DIM}{hint}{RESET}"))
        all_ok &= ok or "Optional" in hint
    print(f"\nMac: {core.host_cpus()} performance cores, {core.host_memory_gb()} GB memory, "
          f"{human_bytes(shutil.disk_usage(os.path.expanduser('~')).free)} free disk")
    if not all_ok:
        sys.exit(1)


def cmd_dashboard(a):
    from . import server
    server.open_dashboard(foreground=a.foreground)


def cmd_supervise(a):
    runtime.supervise(a.name, a.headless)


def main(argv=None):
    p = argparse.ArgumentParser(prog="strata", description="Run Windows 11 on your Mac, for free.")
    p.add_argument("--version", action="version", version=f"Strata {core.VERSION}")
    sub = p.add_subparsers(dest="cmd", metavar="command")

    def add(name, fn, help, aliases=()):
        sp = sub.add_parser(name, help=help, aliases=list(aliases))
        sp.set_defaults(fn=fn)
        return sp

    s = add("dashboard", cmd_dashboard, "open the Strata dashboard in your browser", ["ui"])
    s.add_argument("--foreground", action="store_true", help=argparse.SUPPRESS)
    s = add("create", cmd_create, "create a new Windows 11 VM (downloads Windows automatically)")
    s.add_argument("name", nargs="?")
    s.add_argument("--cpus", type=int)
    s.add_argument("--memory", type=int, help="GB of memory")
    s.add_argument("--disk", type=int, default=64, help="GB of disk (default 64)")
    s.add_argument("--share", help="Mac folder to show in Windows as drive Z:")
    s.add_argument("--user", default="User", help="Windows user name")
    s.add_argument("--password", default="", help="Windows password (blank by default)")
    s = add("start", cmd_start, "start a VM")
    s.add_argument("name", nargs="?")
    s.add_argument("--headless", action="store_true", help="no window (use run/shell/rdp)")
    s = add("stop", cmd_stop, "shut down a VM")
    s.add_argument("name", nargs="?")
    s.add_argument("--force", action="store_true", help="switch off immediately")
    add("list", cmd_list, "list your VMs", ["ls"])
    s = add("info", cmd_info, "show details about a VM")
    s.add_argument("name", nargs="?")
    s = add("run", cmd_run, "run a Windows command and print the output")
    s.add_argument("vm")
    s.add_argument("--ps", action="store_true", help="use PowerShell instead of Command Prompt")
    s.add_argument("--timeout", type=int, default=120)
    s.add_argument("command", nargs=argparse.REMAINDER)
    s = add("shell", cmd_shell, "interactive Windows command prompt in this terminal")
    s.add_argument("name", nargs="?")
    s.add_argument("--ps", action="store_true", help="PowerShell")
    s = add("open", cmd_open, "open an app, file or URL inside Windows (e.g. notepad)")
    s.add_argument("vm")
    s.add_argument("target")
    s = add("snapshot", cmd_snapshot, "save / restore / list / delete snapshots", ["snap"])
    s.add_argument("name", nargs="?")
    s.add_argument("action", nargs="?", choices=["list", "save", "restore", "delete"])
    s.add_argument("tag", nargs="?")
    s = add("set", cmd_set, "change CPUs, memory, shared folder, display or clipboard")
    s.add_argument("name", nargs="?")
    s.add_argument("--cpus", type=int)
    s.add_argument("--memory", type=int, help="GB")
    s.add_argument("--share", help="folder path, or 'off'")
    s.add_argument("--display", choices=["basic", "hd"])
    s.add_argument("--clipboard", choices=["on", "off"])
    s = add("rdp", cmd_rdp, "open in Microsoft's Windows App (sharp, resizable)")
    s.add_argument("name", nargs="?")
    s = add("delete", cmd_delete, "delete a VM permanently")
    s.add_argument("name")
    s.add_argument("-y", "--yes", action="store_true")
    add("download", cmd_download, "download Windows 11 and drivers ahead of time")
    s = add("import-iso", cmd_import_iso, "use a Windows 11 Arm64 ISO you downloaded yourself")
    s.add_argument("path")
    add("doctor", cmd_doctor, "check that everything Strata needs is installed")
    s = sub.add_parser("_supervise")
    s.add_argument("name")
    s.add_argument("--headless", action="store_true")
    s.set_defaults(fn=cmd_supervise)

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
        print("\nCancelled. (Downloads resume where they left off.)")
        sys.exit(130)
