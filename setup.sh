#!/usr/bin/env bash
# Install python deps and verify a UCI engine responds. Usage: ./setup.sh [engine]
set -euo pipefail

# Homebrew and other managed Pythons refuse a bare pip install (PEP 668),
# so only install what is actually missing and say what to run if it fails.
missing=$(python3 - <<'PY'
mods = []
for name, pkg in (("chess", "python-chess"), ("numpy", "numpy"), ("scipy", "scipy")):
    try:
        __import__(name)
    except ImportError:
        mods.append(pkg)
print(" ".join(mods))
PY
)
if [ -n "$missing" ]; then
    echo "installing: $missing"
    python3 -m pip install --quiet $missing || {
        echo "ERROR: pip refused. Try one of:" >&2
        echo "  python3 -m pip install --user $missing" >&2
        echo "  python3 -m pip install --break-system-packages $missing" >&2
        echo "  python3 -m venv .venv && . .venv/bin/activate && ./setup.sh" >&2
        exit 1
    }
fi
echo "OK: python deps (python-chess, numpy, scipy)"

make
python3 -c "import cengine, chess; b=chess.Board(); b.set_castling_fen('-'); \
assert cengine.perft(b, 4) == 197281; print('OK: C core, startpos perft(4) = 197281')"

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
