"""Downloads Windows 11 for Arm from Microsoft, fetches drivers, and builds the hands-free setup disk."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from xml.sax.saxutils import escape

from . import core
from .core import StrataError

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
MS_PAGE = "https://www.microsoft.com/en-us/software-download/windows11arm64"
MS_API = "https://www.microsoft.com/software-download-connector/api"
MS_PROFILE = "606624d44113"
MS_CUSTOMER = "560dc9f3-1aa5-4a2f-b63c-9e18f8d0e175"
VIRTIO_URL = ("https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/"
              "stable-virtio/virtio-win.iso")
GUEST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "guest")
# Microsoft's public generic key: picks the Pro edition during setup, does not activate Windows.
GENERIC_PRO_KEY = "VK7JG-NPHTM-C97JM-9MPGT-3V66T"


def iso_path():
    return os.path.join(core.CACHE_DIR, "Win11_Arm64.iso")


def virtio_path():
    return os.path.join(core.CACHE_DIR, "virtio-win.iso")


# ---------------------------------------------------------------- downloads

def _get(url, referer=MS_PAGE):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer, "Accept-Language": "en-US"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def microsoft_iso_url(language="English"):
    """Asks Microsoft's download site for a fresh (24-hour) Windows 11 Arm64 ISO link."""
    try:
        page = _get(MS_PAGE)
        m = re.search(r'<option value="(\d+)">[^<]*Arm64[^<]*</option>', page, re.I) or \
            re.search(r'<option value="(\d+)">', page)
        if not m:
            raise StrataError("Microsoft's download page changed.")
        edition = m.group(1)
        session = str(uuid.uuid4())
        # Same handshake the website's own scripts perform; without it the API rejects the request.
        js = _get(f"https://ov-df.microsoft.com/mdt.js?instanceId={MS_CUSTOMER}&PageId=si&session_id={session}")
        w = re.search(r"&w=([0-9A-F]+)", js)
        rticks = re.search(r'rticks="\+(\d+)', js)
        if w and rticks:
            _get(f"https://ov-df.microsoft.com/?session_id={session}&CustomerId={MS_CUSTOMER}&PageId=si"
                 f"&w={w.group(1)}&mdt={int(time.time() * 1000)}&rticks={rticks.group(1)}")
        _get(f"https://vlscppe.microsoft.com/tags?org_id=y6jn8c31&session_id={session}")
        skus = json.loads(_get(
            f"{MS_API}/getskuinformationbyproductedition?profile={MS_PROFILE}&ProductEditionId={edition}"
            f"&SKU=undefined&friendlyFileName=undefined&Locale=en-US&sessionID={session}"))["Skus"]
        sku = next((s for s in skus if s["Language"] == language), None) or skus[0]
        links = json.loads(_get(
            f"{MS_API}/GetProductDownloadLinksBySku?profile={MS_PROFILE}&ProductEditionId=undefined"
            f"&SKU={sku['Id']}&friendlyFileName=undefined&Locale=en-US&sessionID={session}"))
        if links.get("Errors"):
            raise StrataError(links["Errors"][0].get("Value", "Microsoft refused the request."))
        return links["ProductDownloadOptions"][0]["Uri"]
    except StrataError as e:
        raise StrataError(f"Couldn't get the Windows download link automatically ({e}). Download the "
                          f"'Windows 11 (multi-edition ISO for Arm64)' from {MS_PAGE} and run: "
                          f"strata import-iso ~/Downloads/<file>.iso")
    except Exception as e:
        raise StrataError(f"Couldn't reach Microsoft's download site ({e}). Check your internet connection.")


def download(url, dest, progress=None, label="Downloading"):
    """Downloads with resume support. progress(label, done_bytes, total_bytes) is called as it goes."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"User-Agent": UA}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416:  # already complete
            os.replace(part, dest)
            return dest
        raise StrataError(f"Download failed: HTTP {e.code}")
    with r:
        if have and r.status != 206:
            have = 0  # server ignored the range request; start over
        total = have + int(r.headers.get("Content-Length") or 0)
        done = have
        last = 0
        with open(part, "ab" if have else "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress and time.time() - last > 0.5:
                    progress(label, done, total)
                    last = time.time()
    if total and done < total:
        raise StrataError("The download was interrupted. Run the same command again to resume.")
    os.replace(part, dest)
    if progress:
        progress(label, done, done)
    return dest


def ensure_windows_iso(progress=None):
    if os.path.exists(iso_path()):
        return iso_path()
    if progress:
        progress("Asking Microsoft for Windows 11", 0, 0)
    return download(microsoft_iso_url(), iso_path(), progress, "Downloading Windows 11 from Microsoft")


def import_iso(path):
    path = os.path.expanduser(path)
    if not os.path.isfile(path) or not path.lower().endswith(".iso"):
        raise StrataError("That isn't an .iso file.")
    os.makedirs(core.CACHE_DIR, exist_ok=True)
    shutil.copyfile(path, iso_path() + ".part")
    os.replace(iso_path() + ".part", iso_path())
    return iso_path()


def ensure_virtio(progress=None):
    if os.path.exists(virtio_path()):
        return virtio_path()
    return download(VIRTIO_URL, virtio_path(), progress, "Downloading Windows drivers")


# ---------------------------------------------------------------- setup disk

def _copy_arm64_drivers(dest):
    """Copies every Windows 11 Arm64 driver folder out of the virtio-win ISO."""
    out = subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noverify", virtio_path()],
                         capture_output=True, text=True)
    mount = next((l.split("\t")[-1].strip() for l in out.stdout.splitlines() if "/Volumes/" in l), None)
    if not mount:
        raise StrataError("Couldn't open the driver disk. Delete ~/.strata/cache/virtio-win.iso and retry.")
    try:
        for driver in sorted(os.listdir(mount)):
            src = os.path.join(mount, driver, "w11", "ARM64")
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(dest, driver))
    finally:
        subprocess.run(["hdiutil", "detach", mount, "-quiet"], check=False)


def unattend_xml(cfg):
    user = escape(cfg["username"])
    pw = escape(cfg.get("password", ""))
    arch = 'processorArchitecture="arm64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"'

    def comp(name, body):
        return f'<component name="{name}" {arch}>{body}</component>'

    def run(order, cmd):
        return (f'<RunSynchronousCommand wcm:action="add"><Order>{order}</Order>'
                f'<Path>{escape(cmd)}</Path></RunSynchronousCommand>')

    locale = ("<InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale>"
              "<UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale>")
    bypass = [run(i + 1, f"reg add HKLM\\SYSTEM\\Setup\\LabConfig /v {k} /t REG_DWORD /d 1 /f")
              for i, k in enumerate(["BypassTPMCheck", "BypassSecureBootCheck", "BypassRAMCheck",
                                     "BypassCPUCheck", "BypassStorageCheck"])]
    first_logon = (
        'cmd /c for %d in (D E F G H I J K L M N O P Q R S T U V W X Y Z) do '
        'if exist %d:\\strata\\install.ps1 powershell -NoProfile -ExecutionPolicy Bypass '
        '-File %d:\\strata\\install.ps1')
    partitions = """
<DiskConfiguration><Disk wcm:action="add"><DiskID>0</DiskID><WillWipeDisk>true</WillWipeDisk>
<CreatePartitions>
 <CreatePartition wcm:action="add"><Order>1</Order><Type>EFI</Type><Size>300</Size></CreatePartition>
 <CreatePartition wcm:action="add"><Order>2</Order><Type>MSR</Type><Size>16</Size></CreatePartition>
 <CreatePartition wcm:action="add"><Order>3</Order><Type>Primary</Type><Extend>true</Extend></CreatePartition>
</CreatePartitions>
<ModifyPartitions>
 <ModifyPartition wcm:action="add"><Order>1</Order><PartitionID>1</PartitionID><Format>FAT32</Format><Label>System</Label></ModifyPartition>
 <ModifyPartition wcm:action="add"><Order>2</Order><PartitionID>2</PartitionID></ModifyPartition>
 <ModifyPartition wcm:action="add"><Order>3</Order><PartitionID>3</PartitionID><Format>NTFS</Format><Label>Windows</Label><Letter>C</Letter></ModifyPartition>
</ModifyPartitions></Disk></DiskConfiguration>"""

    return f"""<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
<settings pass="windowsPE">
{comp("Microsoft-Windows-International-Core-WinPE", f"<SetupUILanguage><UILanguage>en-US</UILanguage></SetupUILanguage>{locale}")}
{comp("Microsoft-Windows-Setup", f'''{partitions}
<ImageInstall><OSImage>
 <InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo>
 <InstallFrom><MetaData wcm:action="add"><Key>/IMAGE/NAME</Key><Value>Windows 11 Pro</Value></MetaData></InstallFrom>
</OSImage></ImageInstall>
<UserData><AcceptEula>true</AcceptEula><ProductKey><Key>{GENERIC_PRO_KEY}</Key><WillShowUI>OnError</WillShowUI></ProductKey></UserData>
<RunSynchronous>{"".join(bypass)}</RunSynchronous>''')}
</settings>
<settings pass="specialize">
{comp("Microsoft-Windows-Shell-Setup", "<ComputerName>STRATA-PC</ComputerName>")}
{comp("Microsoft-Windows-Deployment", "<RunSynchronous>" + run(1, "reg add HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\OOBE /v BypassNRO /t REG_DWORD /d 1 /f") + "</RunSynchronous>")}
</settings>
<settings pass="oobeSystem">
{comp("Microsoft-Windows-International-Core", locale)}
{comp("Microsoft-Windows-Shell-Setup", f'''
<OOBE><HideEULAPage>true</HideEULAPage><HideOnlineAccountScreens>true</HideOnlineAccountScreens>
<HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE><HideLocalAccountScreen>true</HideLocalAccountScreen>
<ProtectYourPC>3</ProtectYourPC></OOBE>
<UserAccounts><LocalAccounts><LocalAccount wcm:action="add">
 <Name>{user}</Name><DisplayName>{user}</DisplayName><Group>Administrators</Group>
 <Password><Value>{pw}</Value><PlainText>true</PlainText></Password>
</LocalAccount></LocalAccounts></UserAccounts>
<AutoLogon><Enabled>true</Enabled><Username>{user}</Username><LogonCount>9999999</LogonCount>
 <Password><Value>{pw}</Value><PlainText>true</PlainText></Password></AutoLogon>
<FirstLogonCommands><SynchronousCommand wcm:action="add"><Order>1</Order>
 <CommandLine>{escape(first_logon)}</CommandLine><Description>Install Strata Tools</Description>
</SynchronousCommand></FirstLogonCommands>''')}
</settings>
</unattend>
"""


def build_setup_iso(cfg, progress=None):
    """Builds the small STRATA disk: autounattend.xml, the helper agent, and Arm64 drivers."""
    if progress:
        progress("Preparing Windows setup disk", 0, 0)
    out = core.vm_path(cfg["name"], "setup.iso")
    with tempfile.TemporaryDirectory() as stage:
        with open(os.path.join(stage, "autounattend.xml"), "w", encoding="utf-8") as f:
            f.write(unattend_xml(cfg))
        tools = os.path.join(stage, "strata")
        shutil.copytree(GUEST_DIR, tools)
        with open(os.path.join(tools, "config.json"), "w") as f:
            json.dump({"token": cfg["token"], "port": 7777, "username": cfg["username"],
                       "blank_password": not cfg.get("password")}, f)
        _copy_arm64_drivers(os.path.join(tools, "drivers"))
        if os.path.exists(out):
            os.remove(out)
        r = subprocess.run(["hdiutil", "makehybrid", "-iso", "-joliet", "-default-volume-name", "STRATA",
                            "-o", out, stage], capture_output=True, text=True)
        if r.returncode != 0:
            raise StrataError(f"Couldn't build the setup disk: {r.stderr.strip()}")
    return out


def create_vm(cfg, progress=None):
    """Full beginner flow: downloads (cached), disks, setup disk. Cleans up on failure."""
    ensure_windows_iso(progress)
    ensure_virtio(progress)
    try:
        core.create_disks(cfg)
        core.save(cfg)
        build_setup_iso(cfg, progress)
    except Exception:
        shutil.rmtree(core.vm_dir(cfg["name"]), ignore_errors=True)
        raise
    return cfg
