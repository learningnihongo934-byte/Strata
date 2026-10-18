#!/bin/bash
# Strata one-line installer:
#   curl -fsSL https://raw.githubusercontent.com/learningnihongo934-byte/Strata/main/install.sh | bash
set -euo pipefail
TARBALL="https://github.com/learningnihongo934-byte/Strata/archive/refs/heads/main.tar.gz"
DEST="$HOME/.strata/app"
BIN="$HOME/.local/bin"
bold() { printf "\033[1m%s\033[0m\n" "$1"; }

bold "Installing Strata: play Windows games on your Mac, for free"
[ "$(uname -m)" = "arm64" ] || { echo "Strata needs an Apple Silicon Mac (M1 or newer)."; exit 1; }

if [ ! -e /Library/Apple/usr/libexec/oah/libRosettaRuntime ]; then
  bold "Installing Rosetta 2 (Apple's translator; may ask for your password)..."
  softwareupdate --install-rosetta --agree-to-license
fi

if ! /usr/bin/python3 -c "" >/dev/null 2>&1; then
  bold "Python is needed. A window will offer to install Apple's Command Line Tools: click Install, then run this installer again."
  xcode-select --install || true
  exit 1
fi

bold "Downloading Strata..."
rm -rf "$DEST.new" && mkdir -p "$DEST.new"
curl -fsSL "$TARBALL" | tar -xz -C "$DEST.new" --strip-components 1
rm -rf "$DEST" && mv "$DEST.new" "$DEST"
chmod +x "$DEST/bin/strata"
mkdir -p "$BIN" && ln -sf "$DEST/bin/strata" "$BIN/strata"

case ":$PATH:" in *":$BIN:"*) ;; *)
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc"
  echo "(Added ~/.local/bin to your PATH; open a new Terminal window to use 'strata'.)" ;;
esac

bold "Done! Opening the Strata dashboard..."
"$DEST/bin/strata" dashboard || true
