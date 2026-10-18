<p align="center"><img src="strata/web/icon.svg" width="96" alt="Strata logo"></p>

<h1 align="center">Strata</h1>
<p align="center"><b>Run Windows 11 on your Mac, for free.</b><br>
A free alternative to Parallels Desktop for Apple Silicon Macs: one install command, a clean dashboard, and Windows sets itself up.</p>

---

## What it does

| | |
|---|---|
| 🪟 **Windows 11 Pro for Arm** | Downloaded straight from Microsoft and installed **automatically**, with no clicks and no Microsoft account |
| ⚡ **Fast** | Uses Apple's built-in Hypervisor, so Windows runs at near-native speed. Most regular (x64) Windows apps work through Windows' built-in emulation |
| 🖱 **Dashboard** | Create, start, stop and configure PCs with buttons in a Mac-style web dashboard |
| 💻 **Windows commands from your Mac** | `strata run windows ipconfig`, an interactive `strata shell`, and a terminal right in the dashboard |
| 📋 **Shared clipboard** | Copy on the Mac, paste in Windows, and the other way round |
| 📁 **Shared folders** | Any Mac folder shows up in Windows as drive **Z:** |
| 📸 **Snapshots** | Save the whole PC, including open apps, and roll back any time |
| 🌐 **Internet & drivers** | Network, display, memory and other drivers are installed for you |
| 🖥 **Sharp, resizable display** | One click opens Windows in Microsoft's free *Windows App* for a crisp, full-resolution, resizable window |
| 🔐 **TPM 2.0** | Emulated, like a real Windows 11 PC |

## Install

You need a Mac with an **M1, M2, M3, M4 or newer** chip, **16 GB of memory recommended** (8 GB works but is tight), and about **40 GB of free disk space**.

Open **Terminal** (press ⌘-Space, type *Terminal*, press Return), paste this and press Return:

```bash
curl -fsSL https://raw.githubusercontent.com/learningnihongo934-byte/Strata/main/install.sh | bash
```

This installs the free tools Strata uses (Homebrew, QEMU, swtpm, Samba), installs Strata and opens the dashboard.

## Use it

### The easy way: the dashboard

```bash
strata dashboard
```

Click **＋ New Windows PC**, choose how much of your Mac to give it, then click **Create & Start**. Strata will:

1. download Windows 11 from Microsoft (about 6 GB, only once),
2. download the drivers,
3. open a window where Windows **installs itself**. It restarts a few times and takes about 15–30 minutes. Don't press anything.

When the Windows desktop appears, the dashboard shows **Running** and the terminal, clipboard and shared folder all switch on.

### From the Terminal

```bash
strata create                       # make a PC called "windows"
strata start                        # turn it on
strata run windows ipconfig         # run a Windows command, see the output on your Mac
strata run windows --ps Get-Process # ...or a PowerShell one
strata shell                        # interactive Windows command prompt
strata open windows notepad         # open an app/file/website inside Windows
strata snapshot windows save clean  # save a snapshot
strata snapshot windows restore clean
strata set windows --share ~/Documents   # share a folder (drive Z:)
strata set windows --memory 8 --cpus 6
strata rdp                          # open in Microsoft's Windows App (sharp & resizable)
strata stop                         # shut down
strata list                         # see all your PCs
strata doctor                       # check your setup
strata --help                       # everything else
```

## Good to know

- **Windows license:** Windows runs unactivated until you enter your own product key in *Settings → System → Activation*. Unactivated Windows works but shows a small watermark and limits personalization.
- **Display:** the built-in window uses a basic display. For a sharp, resizable window, click **Open in Windows App** (get Microsoft's free [Windows App](https://apps.apple.com/app/windows-app/id1295203466) from the Mac App Store), or turn on *High-resolution display* in Settings (experimental).
- **Apps that won't work:** anything that needs an x86 driver, such as some anti-cheat games, some VPNs and some hardware dongles. Regular desktop apps are generally fine.
- **Where things live:** everything is in `~/.strata`. Each PC is a folder in `~/.strata/vms/`. The disk only uses as much space as Windows actually fills.
- **If the automatic download fails:** download *Windows 11 (multi-edition ISO for Arm64)* from [microsoft.com](https://www.microsoft.com/software-download/windows11arm64), then run `strata import-iso ~/Downloads/<file>.iso`.

## How it works

Strata is a small Python program (no extra libraries) that drives **[QEMU](https://www.qemu.org)** with Apple's Hypervisor framework. It never needs a paid Apple developer account because it isn't an App Store app.

- `strata/windows.py` gets a download link from Microsoft's official download site and builds a small "STRATA" setup disk. The disk holds an `autounattend.xml` (which makes Windows install itself, create your user and skip the setup screens), the Arm64 **virtio drivers**, and the Strata helper.
- `guest/agent.ps1` is a small helper that runs inside Windows. The Mac reaches it through a port forward that only listens on your Mac (`127.0.0.1`) and is protected by a per-PC secret token. It runs commands, reads and writes the clipboard, opens apps, and maps the shared folder.
- `strata/runtime.py` runs one supervisor per PC. It starts the TPM emulator and QEMU, presses "boot from CD" on the first boot, and keeps the clipboard in sync.
- `strata/server.py` + `strata/web/` is the dashboard. It only listens on `127.0.0.1`, and requests need a secret token.

## Development

```bash
git clone https://github.com/learningnihongo934-byte/Strata.git
cd Strata
./build.sh            # checks everything and makes dist/strata-<version>.tar.gz
./bin/strata doctor   # run straight from the source folder
```

## License

**PolyForm Noncommercial 1.0.0.** You may use, study and modify Strata for any **non-commercial** purpose. Selling it or using it commercially requires the author's permission. See [LICENSE.md](LICENSE.md).

Strata is not affiliated with Parallels, Microsoft or Apple. Windows is a trademark of Microsoft. QEMU, swtpm, Samba and the virtio-win drivers are separate free software projects under their own licenses, and Strata downloads them; it does not bundle them.
