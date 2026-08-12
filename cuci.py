#!/usr/bin/env python3
"""UCI front end for the C core, so the harness can drive it as an engine.

Speaks the subset python-chess actually sends: uci, isready, ucinewgame,
position, go (nodes/depth/movetime), stop, quit. Unknown commands are ignored
rather than fatal, which is what a UCI host expects.

The point of this engine is coverage, not strength: it takes the positions
Stockfish segfaults on (42 pieces, nine bishops, sixteen pawns), so the 9% of
matchups the arena currently skips become measurable.

  python3 arena.py --engine ./cuci.py ...
"""

import sys

import chess

import cengine

NAME = "SetupCore"
AUTHOR = "IchNukeDichWeg"
DEFAULT_NODES = 20000
MATE = 30000        # search.c MATE
MAX_PLY = 64        # search.c MAX_PLY


def bestmove(board, depth, nodes, movetime):
    # movetime is honoured crudely: the core has no clock, so a time budget is
    # converted to a node budget.
    #
    # UNITS. `movetime` arrives here in SECONDS (the parser divides the UCI
    # milliseconds by 1000), and the old constant 1400 was nodes per
    # MILLISECOND, so every time budget bought 1000x too few nodes: `go
    # movetime 5000` searched 7,000 nodes and returned in about 2ms. Measured
    # throughput of this core on this machine is 2.33 Mnps; 1.4e6 is kept as
    # the conservative figure, since overshooting a clock loses games and
    # undershooting only wastes time.
    # ponytail: replace with a real clock check inside the search when time
    # management starts costing games.
    if movetime and not nodes:
        nodes = max(1000, int(movetime * 1_400_000))
    if not depth and not nodes:
        nodes = DEFAULT_NODES
    move, score, used = cengine.search(board, depth=depth or 64,
                                       nodes=nodes or 0)
    return move, score, used


def main():
    board = chess.Board()
    for line in sys.stdin:
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0]

        if cmd == "uci":
            print("id name %s" % NAME)
            print("id author %s" % AUTHOR)
            print("uciok", flush=True)
        elif cmd == "isready":
            print("readyok", flush=True)
        elif cmd == "ucinewgame":
            board = chess.Board()
        elif cmd == "position":
            # The module docstring promises "Unknown commands are ignored
            # rather than fatal, which is what a UCI host expects", but a
            # MALFORMED ARGUMENT to a known command was fatal: a bad FEN or a
            # bad move token killed the process mid-session, and the host saw
            # EngineTerminatedError. In-repo callers only ever send FENs from
            # rules.setup_fen, so this costs an external or hand-driven host.
            try:
                if "startpos" in parts:
                    nb = chess.Board()
                    rest = parts[parts.index("startpos") + 1:]
                elif "fen" in parts:
                    i = parts.index("fen")
                    end = parts.index("moves") if "moves" in parts else len(parts)
                    nb = chess.Board(" ".join(parts[i + 1:end]))
                    rest = parts[end:]
                else:
                    continue
                if rest and rest[0] == "moves":
                    for tok in rest[1:]:
                        nb.push(chess.Move.from_uci(tok))
            except ValueError as e:
                # keep the previous position rather than dying on it
                print("info string ignoring malformed position: %s" % e,
                      flush=True)
                continue
            board = nb
        elif cmd == "go":
            depth = nodes = movetime = 0
            try:                       # `go depth x` used to kill the process
                for i, tok in enumerate(parts):
                    if tok == "depth" and i + 1 < len(parts):
                        depth = int(parts[i + 1])
                    elif tok == "nodes" and i + 1 < len(parts):
                        nodes = int(parts[i + 1])
                    elif tok == "movetime" and i + 1 < len(parts):
                        movetime = int(parts[i + 1]) / 1000.0
            except ValueError as e:
                print("info string ignoring malformed go argument: %s" % e,
                      flush=True)
                depth = nodes = movetime = 0
            move, score, used = bestmove(board, depth, nodes, movetime)
            if move is None:
                print("bestmove 0000", flush=True)
                continue
            # derived, not hardcoded: search.c's MATE is 30000 and MAX_PLY 64,
            # so changing either in the C file used to break this report
            # silently. cengine exposes them for exactly this reason.
            if abs(score) > MATE - MAX_PLY:
                plies = MATE - abs(score)
                s = "mate %d" % ((plies + 1) // 2 * (1 if score > 0 else -1))
            else:
                s = "cp %d" % score
            # `depth or 1` reported the REQUESTED depth, so every
            # node-limited search claimed depth 1 no matter how deep it went.
            # The core does not return its completed depth, so say what is
            # actually known: the requested depth when one was given, and
            # otherwise nothing false.
            print("info%s score %s nodes %d pv %s"
                  % ((" depth %d" % depth) if depth else "", s, used,
                     move.uci()), flush=True)
            print("bestmove %s" % move.uci(), flush=True)
        elif cmd == "quit":
            return
        # anything else (setoption, stop, ponderhit, debug) is ignored


if __name__ == "__main__":
    main()
