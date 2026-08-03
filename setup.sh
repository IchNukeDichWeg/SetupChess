#!/usr/bin/env bash
# Install python deps and verify a UCI engine responds. Usage: ./setup.sh [engine]
set -euo pipefail

python3 -m pip install --quiet python-chess numpy scipy
echo "OK: python deps (python-chess, numpy, scipy)"

SF="${1:-$(command -v stockfish || true)}"
if [ -z "$SF" ]; then
    echo "ERROR: no stockfish on PATH. Install one (e.g. 'brew install stockfish')" >&2
    echo "       or pass a UCI engine path: ./setup.sh /path/to/engine" >&2
    exit 1
fi
if printf 'uci\nquit\n' | "$SF" | grep -q '^uciok'; then
    echo "OK: $SF answers uci"
else
    echo "ERROR: $SF did not answer uciok" >&2
    exit 1
fi
