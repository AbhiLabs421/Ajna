#!/bin/sh
# Launcher for macOS/Linux. Symlink this as `ajna` somewhere on your PATH.
DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$DIR:$PYTHONPATH" exec python3 -m ajna "$@"
