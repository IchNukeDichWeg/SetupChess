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


def bestmove(board, depth, nodes, movetime):
    # movetime is honoured crudely: the core has no clock, so a time budget is
    # converted to a node budget at a measured ~1.4 Mnps for this search.
    # ponytail: replace with a real clock check inside the search when time
    # management starts costing games.
    if movetime and not nodes:
        nodes = max(1000, int(movetime * 1400))
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
            if "startpos" in parts:
                board = chess.Board()
                rest = parts[parts.index("startpos") + 1:]
            elif "fen" in parts:
                i = parts.index("fen")
                end = parts.index("moves") if "moves" in parts else len(parts)
                board = chess.Board(" ".join(parts[i + 1:end]))
                rest = parts[end:]
            else:
                continue
            if rest and rest[0] == "moves":
                for tok in rest[1:]:
                    board.push(chess.Move.from_uci(tok))
        elif cmd == "go":
            depth = nodes = movetime = 0
            for i, tok in enumerate(parts):
                if tok == "depth" and i + 1 < len(parts):
                    depth = int(parts[i + 1])
                elif tok == "nodes" and i + 1 < len(parts):
                    nodes = int(parts[i + 1])
                elif tok == "movetime" and i + 1 < len(parts):
                    movetime = int(parts[i + 1]) / 1000.0
            move, score, used = bestmove(board, depth, nodes, movetime)
            if move is None:
                print("bestmove 0000", flush=True)
                continue
            if abs(score) > 29000:
                plies = 30000 - abs(score)
                s = "mate %d" % ((plies + 1) // 2 * (1 if score > 0 else -1))
            else:
                s = "cp %d" % score
            print("info depth %d score %s nodes %d pv %s"
                  % (depth or 1, s, used, move.uci()), flush=True)
            print("bestmove %s" % move.uci(), flush=True)
        elif cmd == "quit":
            return
        # anything else (setoption, stop, ponderhit, debug) is ignored


if __name__ == "__main__":
    main()
