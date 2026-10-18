"""Paths, VM configuration, and the QEMU/swtpm process management behind every Strata VM."""

import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import time

VERSION = "1.0.0"

HOME = os.path.expanduser(os.environ.get("STRATA_HOME", "~/.strata"))
VMS_DIR = os.path.join(HOME, "vms")
CACHE_DIR = os.path.join(HOME, "cache")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


class StrataError(Exception):
    """An error with a message that is safe and helpful to show a beginner."""


# ---------------------------------------------------------------- host info

def sysctl(key, default=0):
    try:
        return int(subprocess.check_output(["sysctl", "-n", key], text=True).strip())
    except Exception:
        return default


def host_memory_gb():
    return max(1, sysctl("hw.memsize") // (1024 ** 3))


def host_cpus():
    return max(1, sysctl("hw.physicalcpu", 4))


def host_performance_cpus():
    return max(1, sysctl("hw.perflevel0.physicalcpu") or host_cpus())


def default_memory_gb():
    return max(4, min(8, host_memory_gb() // 2))


def default_cpus():
    return max(2, min(6, host_performance_cpus()))


def brew_prefix():
    for p in ("/opt/homebrew", "/usr/local"):
        if os.path.isdir(p):
            return p
    return "/opt/homebrew"


def find_tool(name):
    return shutil.which(name) or next(
        (p for p in (os.path.join(brew_prefix(), d, name) for d in ("bin", "sbin")) if os.path.exists(p)), None)


def qemu_binary():
    return find_tool("qemu-system-aarch64")


def firmware_dir():
    q = qemu_binary()
    if not q:
        return None
    share = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(q))), "share", "qemu")
    return share if os.path.isdir(share) else os.path.join(brew_prefix(), "share", "qemu")


def doctor():
    """Returns a list of (check, ok, hint) tuples describing the host setup."""
    arm = os.uname().machine == "arm64"
    fw = firmware_dir()
    return [
        ("Apple Silicon Mac", arm, "Strata runs Windows 11 for Arm and needs an M-series Mac."),
        ("QEMU", bool(qemu_binary()), "Run: brew install qemu"),
        ("UEFI firmware", bool(fw and os.path.exists(os.path.join(fw, "edk2-aarch64-code.fd"))),
         "Reinstall QEMU: brew reinstall qemu"),
        ("TPM emulator (swtpm)", bool(find_tool("swtpm")), "Optional. Run: brew install swtpm"),
        ("Shared folders (samba)", bool(find_tool("samba-dot-org-smbd")), "Optional. Run: brew install samba"),
    ]


# ---------------------------------------------------------------- VM records

def vm_dir(name):
    return os.path.join(VMS_DIR, name)


def vm_path(name, *parts):
    return os.path.join(vm_dir(name), *parts)


def validate_name(name):
    if not name or not NAME_RE.match(name):
        raise StrataError("VM names can use letters, numbers, - and _ (up to 32 characters), e.g. 'windows'.")


def list_names():
    if not os.path.isdir(VMS_DIR):
        return []
    return sorted(n for n in os.listdir(VMS_DIR) if os.path.exists(vm_path(n, "vm.json")))


def load(name):
    validate_name(name)
    try:
        with open(vm_path(name, "vm.json")) as f:
            return json.load(f)
    except FileNotFoundError:
        raise StrataError(f"No VM called '{name}'. See your VMs with: strata list")


def save(cfg):
    path = vm_path(cfg["name"], "vm.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


def update(name, **changes):
    cfg = load(name)
    cfg.update(changes)
    save(cfg)
    return cfg


def _free_port_pair():
    used = set()
    for n in list_names():
        try:
            c = load(n)
            used.update((c.get("agent_port"), c.get("rdp_port")))
        except Exception:
            pass
    port = 47700
    while port in used or port + 1 in used:
        port += 2
    return port, port + 1


def new_config(name, cpus=None, memory_gb=None, disk_gb=64, share=None, username="User", password=""):
    validate_name(name)
    if os.path.exists(vm_dir(name)):
        raise StrataError(f"A VM called '{name}' already exists. Pick another name or delete it first.")
    agent_port, rdp_port = _free_port_pair()
    return {
        "name": name,
        "os": "windows11-arm64",
        "cpus": int(cpus or default_cpus()),
        "memory_gb": int(memory_gb or default_memory_gb()),
        "disk_gb": int(disk_gb),
        "share": share or None,
        "username": username or "User",
        "password": password or "",
        "agent_port": agent_port,
        "rdp_port": rdp_port,
        "token": secrets.token_hex(16),
        "installed": False,
        "display": "basic",
        "created": int(time.time()),
    }


# ---------------------------------------------------------------- disks

def qemu_img(*args):
    tool = find_tool("qemu-img")
    if not tool:
        raise StrataError("qemu-img is missing. Run: brew install qemu")
    r = subprocess.run([tool, *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise StrataError((r.stderr or r.stdout).strip() or "qemu-img failed")
    return r.stdout


def create_disks(cfg):
    os.makedirs(vm_dir(cfg["name"]), exist_ok=True)
    os.makedirs(vm_path(cfg["name"], "tpm"), exist_ok=True)
    qemu_img("create", "-f", "qcow2", vm_path(cfg["name"], "disk.qcow2"), f"{cfg['disk_gb']}G")
    # UEFI settings live in a qcow2 copy of the blank vars file so live snapshots include them.
    qemu_img("convert", "-O", "qcow2", os.path.join(firmware_dir(), "edk2-arm-vars.fd"),
             vm_path(cfg["name"], "vars.qcow2"))


def disk_usage_bytes(name):
    total = 0
    for f in ("disk.qcow2", "vars.qcow2"):
        try:
            total += os.stat(vm_path(name, f)).st_blocks * 512
        except OSError:
            pass
    return total


# ---------------------------------------------------------------- processes

def _read_pid(path):
    try:
        with open(path) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def qemu_pid(name):
    return _read_pid(vm_path(name, "qemu.pid"))


def supervisor_pid(name):
    return _read_pid(vm_path(name, "supervisor.pid"))


def is_running(name):
    return qemu_pid(name) is not None


def runtime_status(name):
    try:
        with open(vm_path(name, "status.json")) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    if not is_running(name):
        st = {}
    return st


def write_status(name, **status):
    path = vm_path(name, "status.json")
    with open(path + ".tmp", "w") as f:
        json.dump(status, f)
    os.replace(path + ".tmp", path)


def qemu_args(cfg, headless=False):
    name = cfg["name"]
    fw = firmware_dir()
    from . import windows  # late import: windows imports core

    hostfwd = (f"hostfwd=tcp:127.0.0.1:{cfg['agent_port']}-:7777,"
               f"hostfwd=tcp:127.0.0.1:{cfg['rdp_port']}-:3389")
    net = f"user,id=net0,{hostfwd}"
    if cfg.get("share") and os.path.isdir(cfg["share"]) and find_tool("samba-dot-org-smbd"):
        if "," in cfg["share"]:
            raise StrataError("The shared folder path can't contain a comma. Pick or rename the folder.")
        net += f",smb={cfg['share']}"

    args = [
        qemu_binary(),
        "-name", f"Strata - {name}",
        "-machine", "virt,highmem=on",
        "-accel", "hvf", "-cpu", "host",
        "-smp", str(cfg["cpus"]), "-m", f"{cfg['memory_gb']}G",
        "-rtc", "base=localtime",
        "-drive", f"if=pflash,format=raw,readonly=on,file={os.path.join(fw, 'edk2-aarch64-code.fd')}",
        "-drive", f"if=pflash,format=qcow2,file={vm_path(name, 'vars.qcow2')}",
        "-device", "virtio-gpu-pci" if cfg.get("display") == "hd" else "ramfb",
        "-device", "qemu-xhci,id=xhci",
        "-device", "usb-kbd", "-device", "usb-tablet",
        "-drive", f"if=none,id=hd0,format=qcow2,discard=unmap,file={vm_path(name, 'disk.qcow2')}",
        "-device", "nvme,drive=hd0,serial=STRATA0,bootindex=1",
        "-netdev", net, "-device", "virtio-net-pci,netdev=net0",
        "-device", "virtio-balloon-pci",
        "-monitor", f"unix:{vm_path(name, 'monitor.sock')},server,nowait",
        "-pidfile", vm_path(name, "qemu.pid"),
    ]
    if not cfg.get("installed") and os.path.exists(windows.iso_path()):
        args += ["-drive", f"if=none,id=cd0,media=cdrom,readonly=on,file={windows.iso_path()}",
                 "-device", "usb-storage,drive=cd0,bootindex=0"]
    if os.path.exists(vm_path(name, "setup.iso")):
        args += ["-drive", f"if=none,id=cd1,media=cdrom,readonly=on,file={vm_path(name, 'setup.iso')}",
                 "-device", "usb-storage,drive=cd1"]
    if os.path.exists(vm_path(name, "tpm.sock")):
        args += ["-chardev", f"socket,id=chrtpm,path={vm_path(name, 'tpm.sock')}",
                 "-tpmdev", "emulator,id=tpm0,chardev=chrtpm",
                 "-device", "tpm-tis-device,tpmdev=tpm0,ppi=off"]
    args += ["-display", "none"] if headless else ["-display", "cocoa"]
    return args


def start_tpm(name):
    swtpm = find_tool("swtpm")
    if not swtpm:
        return False
    sock = vm_path(name, "tpm.sock")
    if os.path.exists(sock):
        os.remove(sock)
    subprocess.run([swtpm, "socket", "--tpm2",
                    "--tpmstate", f"dir={vm_path(name, 'tpm')}",
                    "--ctrl", f"type=unixio,path={sock},terminate",
                    "--log", f"file={vm_path(name, 'tpm.log')}",
                    "--daemon"], check=False)
    for _ in range(50):
        if os.path.exists(sock):
            return True
        time.sleep(0.1)
    return False


# ---------------------------------------------------------------- QEMU monitor

def monitor(name, command, timeout=10):
    """Sends one human-monitor command to a running VM and returns its output."""
    path = vm_path(name, "monitor.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
    except OSError:
        raise StrataError(f"'{name}' isn't running.")

    def read_prompt():
        buf = b""
        while not buf.endswith(b"(qemu) "):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return buf.decode("utf-8", "replace")

    try:
        read_prompt()
        s.sendall(command.encode() + b"\n")
        out = read_prompt()
    except socket.timeout:
        raise StrataError(f"The VM didn't answer '{command.split()[0]}' in time.")
    except OSError:
        raise StrataError(f"'{name}' stopped responding.")
    finally:
        s.close()
    # Drop the echoed command line (with terminal escape codes) and the trailing prompt.
    out = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out).replace("\r", "")
    lines = out.split("\n")
    if lines and command.split()[0] in lines[0]:
        lines = lines[1:]
    return "\n".join(l for l in lines if l.strip() != "(qemu)").strip()


def stop(name, force=False, wait=60):
    pid = qemu_pid(name)
    if not pid:
        return False
    if force:
        try:
            monitor(name, "quit", timeout=3)
        except StrataError:
            os.kill(pid, signal.SIGTERM)
    else:
        monitor(name, "system_powerdown")
    deadline = time.time() + (10 if force else wait)
    while time.time() < deadline and qemu_pid(name):
        time.sleep(0.5)
    return qemu_pid(name) is None


# ---------------------------------------------------------------- snapshots

_SNAP_LINE = re.compile(r"^\s*(?:\d+|--)\s+(\S+)\s+(\S+(?:\s\S+)?)\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")


def list_snapshots(name):
    if is_running(name):
        out = monitor(name, "info snapshots")
    else:
        out = qemu_img("snapshot", "-l", vm_path(name, "disk.qcow2"))
    snaps = []
    for line in out.splitlines():
        m = _SNAP_LINE.match(line)
        if m and m.group(1) != "TAG":
            snaps.append({"tag": m.group(1), "size": m.group(2), "date": m.group(3)})
    return snaps


def validate_tag(tag):
    if not tag or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,40}$", tag):
        raise StrataError("Snapshot names can use letters, numbers, '.', '-' and '_' (no spaces).")


def _check_monitor_output(out):
    if out and re.search(r"error|failed|not found|does not", out, re.I):
        raise StrataError(out.splitlines()[-1])


def snapshot(name, action, tag=None):
    if action != "list":
        validate_tag(tag)
    if action == "list":
        return list_snapshots(name)
    if is_running(name):
        cmd = {"save": "savevm", "restore": "loadvm", "delete": "delvm"}[action]
        _check_monitor_output(monitor(name, f"{cmd} {tag}", timeout=600))
    else:
        flag = {"save": "-c", "restore": "-a", "delete": "-d"}[action]
        for f in ("disk.qcow2", "vars.qcow2"):
            try:
                qemu_img("snapshot", flag, tag, vm_path(name, f))
            except StrataError:
                if f == "disk.qcow2":
                    raise
    return True


def delete_vm(name):
    validate_name(name)
    if is_running(name):
        stop(name, force=True)
    if os.path.isdir(vm_dir(name)):
        shutil.rmtree(vm_dir(name))
