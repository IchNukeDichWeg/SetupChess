"""Fill the payoff matrix: setup i against setup j, engine vs engine.

P[i][j] is the expected score of setup i against setup j, averaged over games
with the colours swapped, so a cell is never contaminated by the first-move
advantage. Scores are always from i's perspective.

Why the games are jittered: a UCI engine at a fixed node count is
deterministic, so replaying the same pair would return the same game every
time and N games would buy nothing. Each game therefore gets its own node
count drawn from a band around --nodes, seeded from (i, j, game index) so a
resumed run reproduces the same games it would have played. Without this the
P[i][i] == 0.50 sanity check is vacuous: it holds by construction rather than
telling you anything about colour bias.

The whole file is CLI-driven and writes only to --out, which has no default.
"""

import argparse
import json
import multiprocessing as mp
import os
import random
import sys
import time

import chess
import chess.engine

import pool as poolmod
import rules

# 300 plies is well past where a decided Setup Chess game resolves; a game
# still running at that point is scored as a draw rather than left hanging.
PLY_LIMIT = 300

_ENGINE = None
_ENGINE_PATH = None


class Unplayable(Exception):
    """The position is legal Setup Chess but not safe for this engine."""


def worker_init(engine_path):
    global _ENGINE, _ENGINE_PATH
    _ENGINE_PATH = engine_path
    _ENGINE = chess.engine.SimpleEngine.popen_uci(engine_path)


def _worker_engine():
    """Restart the engine if it died mid-campaign rather than losing the run."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = chess.engine.SimpleEngine.popen_uci(_ENGINE_PATH)
    return _ENGINE


def game_nodes(base, jitter, i, j, g, color):
    """Deterministic per-game node count. Same inputs -> same game, always."""
    if jitter <= 0:
        return base
    rng = random.Random((i, j, g, color).__hash__())
    return max(1, int(base * (1.0 + rng.uniform(-jitter, jitter))))


def play_game(white_army, black_army, engine, nodes):
    """Returns White's score (1.0 / 0.5 / 0.0) and the ply count."""
    fen = rules.setup_fen(white_army, black_army)
    ok, why = rules.engine_safe(fen)
    if not ok:
        raise Unplayable("%s (%s)" % (why, fen))
    board = chess.Board(fen)
    limit = chess.engine.Limit(nodes=nodes)
    while not board.is_game_over(claim_draw=True) and board.ply() < PLY_LIMIT:
        result = engine.play(board, limit)
        if result.move is None:
            break
        board.push(result.move)
    outcome = board.outcome(claim_draw=True)
    if outcome is None or outcome.winner is None:
        return 0.5, board.ply()
    return (1.0 if outcome.winner == chess.WHITE else 0.0), board.ply()


def play_pair(ai, aj, nodes, jitter, i, j, g):
    """Both colours of one matchup. Returns the two scores from ai's side.

    Raises Unplayable before any game starts, so a cell that the engine
    cannot take is recorded once rather than half-played.
    """
    for w, b in ((ai, aj), (aj, ai)):
        ok, why = rules.engine_safe(rules.setup_fen(w, b))
        if not ok:
            raise Unplayable(why)
    engine = _worker_engine()
    w, ply_w = play_game(ai, aj, engine, game_nodes(nodes, jitter, i, j, g, 0))
    b, ply_b = play_game(aj, ai, engine, game_nodes(nodes, jitter, i, j, g, 1))
    return (w, 1.0 - b), ply_w + ply_b


def play_cell(task):
    """One game pair for cell (i, j): i as White, then i as Black."""
    i, j, g, armies, nodes, jitter = task
    ai, aj = armies
    try:
        (w, b), plies = play_pair(ai, aj, nodes, jitter, i, j, g)
    except Unplayable as e:
        return i, j, g, "unplayable", str(e)
    except (chess.engine.EngineError, chess.engine.EngineTerminatedError) as e:
        global _ENGINE
        _ENGINE = None
        return i, j, g, None, str(e)
    # both games scored from i's perspective, then averaged
    return i, j, g, (w + b) / 2.0, plies


def play_match(task):
    """A standalone matchup outside the matrix: returns per-game scores.

    Used by the final gate match, which needs independent games for a real
    confidence interval rather than averaged cells.
    """
    tag, ai, aj, nodes, jitter, k = task
    try:
        scores, _plies = play_pair(ai, aj, nodes, jitter, k, k + 1, k)
    except Unplayable as e:
        return tag, None, str(e)
    except (chess.engine.EngineError, chess.engine.EngineTerminatedError) as e:
        global _ENGINE
        _ENGINE = None
        return tag, None, str(e)
    return tag, list(scores), ""


def load_state(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        state = json.load(f)
    return {tuple(int(x) for x in k.split(",")): v
            for k, v in state.get("cells", {}).items()}


def save_state(path, cells, meta):
    """Atomic replace via a sibling temp file; never deletes the old file
    before the new one is complete."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"meta": meta,
                   "cells": {"%d,%d,%d" % k: v for k, v in cells.items()}},
                  f)
    os.replace(tmp, path)


def matrix_from_cells(cells, n):
    """Average the game pairs per cell. Missing cells come back as None."""
    acc = {}
    for (i, j, _g), v in cells.items():
        if v is None:  # unplayable cell, recorded so it is not retried
            continue
        acc.setdefault((i, j), []).append(v)
    return [[(sum(acc[(i, j)]) / len(acc[(i, j)]) if (i, j) in acc else None)
             for j in range(n)] for i in range(n)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True,
                    help="results JSON, resumed if it exists (no default)")
    ap.add_argument("--pool", help="pool JSON; omit to use the archetypes")
    ap.add_argument("--engine", default="stockfish", help="UCI binary")
    ap.add_argument("--nodes", type=int, default=20000)
    ap.add_argument("--jitter", type=float, default=0.15,
                    help="node-count band, 0 for none (see module docstring)")
    ap.add_argument("--pairs", type=int, default=1,
                    help="game pairs per cell; each pair is 2 games")
    ap.add_argument("--workers", type=int, default=0, help="0 = all cores")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    if args.pool:
        with open(args.pool) as f:
            armies = [poolmod.from_json(a) for a in json.load(f)]
    else:
        armies = poolmod.seed_pool(rng)
    n = len(armies)

    for idx, army in enumerate(armies):
        ok, why = rules.validate_army(army)
        if not ok:
            sys.exit("army %d is illegal: %s" % (idx, why))

    cells = load_state(args.out)
    tasks = [(i, j, g, (armies[i], armies[j]), args.nodes, args.jitter)
             for i in range(n) for j in range(n)
             for g in range(args.pairs) if (i, j, g) not in cells]
    workers = args.workers or os.cpu_count()
    meta = {"n": n, "nodes": args.nodes, "jitter": args.jitter,
            "pairs": args.pairs, "engine": os.path.basename(args.engine),
            "seed": args.seed, "ply_limit": PLY_LIMIT}

    print("%d setups, %d cells, %d game pairs to play (%d already done), "
          "%d workers" % (n, n * n, len(tasks), len(cells), workers))
    if not tasks:
        print("nothing to do")
        return

    start = time.time()
    done = errors = unplayable = 0
    with mp.Pool(workers, initializer=worker_init,
                 initargs=(args.engine,)) as p:
        for i, j, g, score, info in p.imap_unordered(play_cell, tasks):
            done += 1
            if score == "unplayable":
                unplayable += 1
                cells[(i, j, g)] = None
            elif score is None:
                errors += 1
                print("  engine error on (%d,%d,%d): %s" % (i, j, g, info))
            else:
                cells[(i, j, g)] = score
            if done % 25 == 0 or done == len(tasks):
                save_state(args.out, cells, meta)
                rate = done / max(1e-9, time.time() - start)
                print("  %d/%d pairs, %.2f pairs/s, eta %.1f min, %d errors"
                      % (done, len(tasks), rate,
                         (len(tasks) - done) / max(rate, 1e-9) / 60, errors))
    save_state(args.out, cells, meta)

    m = matrix_from_cells(cells, n)
    diag = [m[i][i] for i in range(n) if m[i][i] is not None]
    sym = [m[i][j] + m[j][i] for i in range(n) for j in range(n)
           if m[i][j] is not None and m[j][i] is not None]
    print("wrote %s" % args.out)
    if diag:
        print("P[i][i] mean %.4f (want 0.50), min %.3f max %.3f"
              % (sum(diag) / len(diag), min(diag), max(diag)))
    if sym:
        print("P[i][j]+P[j][i] mean %.4f (want 1.00), min %.3f max %.3f"
              % (sum(sym) / len(sym), min(sym), max(sym)))
    if unplayable:
        print("%d game pairs skipped: too many pieces for this engine "
              "(rules.ENGINE_MAX_PIECES)" % unplayable)
    if errors:
        print("%d game pairs failed and were not recorded; re-run to fill them"
              % errors)


if __name__ == "__main__":
    main()
