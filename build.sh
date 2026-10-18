#!/bin/bash
# Checks the code and packages a release tarball in dist/. Strata is plain Python + PowerShell,
# so "building" means validating everything and bundling it.
set -euo pipefail
cd "$(dirname "$0")"
VERSION=$(python3 -c 'import sys; sys.path.insert(0,"."); from strata.core import VERSION; print(VERSION)')

echo "Checking Python..."
python3 -m py_compile strata/*.py
echo "Checking shell scripts..."
bash -n install.sh bin/strata
echo "Checking the dashboard script..."
if command -v node >/dev/null; then node --check strata/web/app.js; fi
echo "Smoke test..."
STRATA_HOME="$(mktemp -d)" ./bin/strata --version
STRATA_HOME="$(mktemp -d)" ./bin/strata games >/dev/null

mkdir -p dist
tar --exclude="__pycache__" -czf "dist/strata-$VERSION.tar.gz" bin strata install.sh README.md LICENSE.md
echo "Built dist/strata-$VERSION.tar.gz"
