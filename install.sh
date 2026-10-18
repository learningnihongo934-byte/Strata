#!/bin/bash
# Strata one-line installer:
#   curl -fsSL https://raw.githubusercontent.com/learningnihongo934-byte/Strata/main/install.sh | bash
set -euo pipefail

REPO="https://github.com/learningnihongo934-byte/Strata.git"
DEST="$HOME/.strata/app"
bold() { printf "\033[1m%s\033[0m\n" "$1"; }

bold "Installing Strata — run Windows 11 on your Mac, for free"

if [ "$(uname -m)" != "arm64" ]; then
  echo "Strata needs an Apple Silicon Mac (M1 or newer)."; exit 1
fi

if ! command -v brew >/dev/null 2>&1 && [ ! -x /opt/homebrew/bin/brew ]; then
  bold "Installing Homebrew (the free Mac package manager). It may ask for your Mac password."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
eval "$(/opt/homebrew/bin/brew shellenv)"

bold "Installing QEMU, TPM emulator and Samba (a few minutes)..."
brew install qemu swtpm samba git python3 >/dev/null

bold "Downloading Strata..."
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull --ff-only --quiet
else
  rm -rf "$DEST"; mkdir -p "$(dirname "$DEST")"
  git clone --depth 1 --quiet "$REPO" "$DEST"
fi
chmod +x "$DEST/bin/strata"
ln -sf "$DEST/bin/strata" "$(brew --prefix)/bin/strata"

echo
bold "Done! Strata is installed."
echo "  Open the dashboard:   strata dashboard"
echo "  Or use the terminal:  strata create   then   strata start"
echo
"$DEST/bin/strata" doctor || true
"$DEST/bin/strata" dashboard || true
