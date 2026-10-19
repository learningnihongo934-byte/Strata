<p align="center"><img src="strata/web/icon.svg" width="96" alt="Strata logo"></p>

<h1 align="center">Strata</h1>
<p align="center"><b>Play Windows games on your Mac, for free.</b><br>
Steam, Epic Games, GOG and any Windows game on Apple Silicon. Games run on your Mac's GPU through Apple's Game Porting Toolkit. No Windows license, no virtual machine.</p>

---

## Features

| | |
|---|---|
| 🎮 **Runs Windows games without Windows** | Built on Wine + Apple's **D3DMetal**, which translates DirectX 11 and 12 straight to Metal so games use your Mac's real GPU |
| 🛒 **Steam, Epic Games, GOG Galaxy** | One-click install of each store. Nothing downloads until you click |
| 📦 **Any Windows game** | Run any installer (`.exe` / `.msi`) and add any game `.exe` to your library |
| 📚 **Automatic library** | Games you install through Steam, Epic or GOG appear automatically, with cover art |
| 🚀 **Dock icons** | One click turns any game into a Mac app in your Dock |
| ⚙️ **Per-game switches** | FPS overlay, MetalFX upscaling, ray tracing, Retina, ESync, AVX: each one on or off per game |
| 🖱 **Dashboard + Terminal** | A Mac-style dashboard, or `strata play "game"` from the Terminal |

## Install

You need a Mac with an **Apple M-series chip** and **macOS 14 Sonoma or newer**. 16 GB of memory is recommended.

Open **Terminal** (⌘-Space, type *Terminal*, press Return), paste this and press Return:

```bash
curl -fsSL https://raw.githubusercontent.com/learningnihongo934-byte/Strata/main/install.sh | bash
```

The dashboard opens. Click **Set up Strata**, which downloads the game engine once (about 240 MB). Then go to **Stores** and install Steam, Epic or GOG.

## Terminal commands

```bash
strata dashboard                     # open the dashboard
strata setup                         # download the engine, create the Windows environment
strata store install steam           # install a store (steam, epic, gog)
strata store open steam              # open it
strata games                         # list your games
strata play "Hollow Knight"          # play (name or id)
strata settings "Hollow Knight" hud=on retina=off   # per-game switches
strata dock "Hollow Knight"          # add the game to your Dock
strata run ~/Downloads/setup.exe     # run any Windows installer
strata add "/path/to/Game.exe"       # add a game to your library
strata stop                          # close all games and stores
strata doctor                        # check your Mac
```

## What works and what doesn't

- ✅ Most **single-player** DirectX 11 and 12 games, many DirectX 9 games, and indie games.
- ⚠️ Some games need a setting changed. Try turning **AVX** or **ESync** off in the game's ⚙︎ settings.
- ❌ Games with **kernel anti-cheat** (Fortnite, Valorant, Apex Legends, many online shooters) can't run this way on any Mac. Use cloud gaming such as GeForce Now or Xbox Cloud for those.
- Check a game's compatibility on [AppleGamingWiki](https://www.applegamingwiki.com).

## How it works

- `strata/engine.py` downloads [Gcenx's free build](https://github.com/Gcenx/game-porting-toolkit) of Apple's **Game Porting Toolkit** (Wine + D3DMetal) into `~/.strata/runtime`, and keeps one shared Windows environment in `~/.strata/bottle`.
- `strata/games.py` installs the stores, reads their libraries (Steam `appmanifest` files, Epic manifests, GOG `goggame-*.info`), launches games with your per-game switches as environment variables, and builds Dock apps.
- `strata/server.py` + `strata/web/` is the dashboard. It only listens on `127.0.0.1`, and requests need a secret token.

Everything Strata stores is in `~/.strata`. Dock apps go to `~/Applications/Strata Games`. To uninstall, delete both folders.

## Development

```bash
git clone https://github.com/learningnihongo934-byte/Strata.git && cd Strata
./build.sh          # checks everything and packages dist/strata-<version>.tar.gz
./bin/strata doctor
```

## License

**Free to use; copying, redistributing or selling it is not allowed without the author's permission.** See [LICENSE.md](LICENSE.md).

Strata downloads, and does not include, the Game Porting Toolkit. Its D3DMetal component is © Apple and subject to Apple's license, which allows non-commercial use only. Wine is LGPL. Strata is not affiliated with Apple, Valve, Epic Games, GOG or Parallels.
