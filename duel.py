"""Engine versus engine over Setup Chess positions.

arena.py measures ARMIES and uses one engine as the referee; this measures the
ENGINES and uses the armies as the opening book. Positions come from the pool,
so the material is the strange kind this project actually produces rather than
ordinary chess.

Equal NODES, not equal time. That is a search-efficiency comparison, and it is
the one this infrastructure can make honestly: our core has no clock, so
cuci.py converts a movetime into a node estimate and an equal-time match would
be measuring that conversion as much as the engines. Equal time is the fairer
question and it needs real time management first.

Only positions both engines can take are played, so the comparison is over a
common subset. The count of positions excluded for the other engine's sake is
reported rather than hidden -- for Stockfish that is the high-piece-count
matchups, which is exactly where our core is the only option.

Resumable through --out, which has no default.
"""

import argparse
import json
import multiprocessing as mp
import os
import random
import signal
import sys

import chess
import chess.engine

import arena
import pool as poolmod
import rules
import stats

_A = _B = None
_PATHS = (None, None)


def worker_init(path_a, path_b):
    global _A, _B, _PATHS
    _PATHS = (path_a, path_b)
    _A = chess.engine.SimpleEngine.popen_uci(path_a)
    _B = chess.engine.SimpleEngine.popen_uci(path_b)


def _engines():
    global _A, _B
    if _A is None:
        _A = chess.engine.SimpleEngine.popen_uci(_PATHS[0])
    if _B is None:
        _B = chess.engine.SimpleEngine.popen_uci(_PATHS[1])
    return _A, _B


def _play(fen, white, black, nodes):
    """One game. Returns White's score."""
    board = chess.Board(fen)
    limit = chess.engine.Limit(nodes=nodes)
    while (not board.is_game_over(claim_draw=True)
           and board.ply() < arena.PLY_LIMIT):
        engine = white if board.turn == chess.WHITE else black
        mv = engine.play(board, limit).move
        if mv is None:
            break
        board.push(mv)
    outcome = board.outcome(claim_draw=True)
    if outcome is None or outcome.winner is None:
        return 0.5
    return 1.0 if outcome.winner == chess.WHITE else 0.0


def _pair(task):
    """A as White then A as Black. Returns the two scores from A's side."""
    k, ai, aj, nodes, jitter, both_safe = task
    fen = rules.setup_fen(ai, aj)
    if not rules.validate_fen(fen)[0]:
        return k, None, "invalid"
    if both_safe and not rules.engine_safe(fen)[0]:  # the reference's ceiling
        return k, None, "unsafe for the reference engine"
    try:
        a, b = _engines()
        s1 = _play(fen, a, b, arena.game_nodes(nodes, jitter, k, k + 1, k, 0))
        s2 = _play(fen, b, a, arena.game_nodes(nodes, jitter, k, k + 1, k, 1))
    except (chess.engine.EngineError,
            chess.engine.EngineTerminatedError) as e:
        global _A, _B
        _A = _B = None
        return k, None, str(e)
    return k, [s1, 1.0 - s2], ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engine-a", default="./cuci.py", help="engine under test")
    ap.add_argument("--engine-b", default="stockfish", help="the reference")
    ap.add_argument("--pool", default="campaigns/expand_v2.json",
                    help="campaign state supplying the opening armies")
    ap.add_argument("--out", required=True, help="results JSON (no default)")
    ap.add_argument("--games", type=int, default=200, help="paired games")
    ap.add_argument("--nodes", type=int, default=20000)
    ap.add_argument("--jitter", type=float, default=0.15)
    ap.add_argument("--all-positions", action="store_true",
                    help="include positions the reference engine cannot take "
                         "(it will crash; only useful against another core)")
    ap.add_argument("--workers", type=int, default=0, help="0 = all cores")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    done = {}
    key = (args.engine_a, args.engine_b, args.nodes, args.jitter, args.pool)
    if os.path.exists(args.out):
        with open(args.out) as f:
            prev = json.load(f)
        if tuple(prev.get("key", [])) != key:
            sys.exit("existing %s was made with different settings" % args.out)
        done = {int(k): v for k, v in prev.get("by_index", {}).items()}
        print("resuming: %d pairs already played" % len(done))

    with open(args.pool) as f:
        armies = [poolmod.from_json(a) for a in json.load(f)["armies"]]
    field = [poolmod.complete(a, random.Random(0))
             for a in poolmod.ARCHETYPES.values()] + armies
    rng = random.Random(args.seed)

    tasks = []
    for k in range(args.games):
        if k in done:
            continue
        ai = field[k % len(field)]
        aj = field[(k * 7 + 3) % len(field)]
        tasks.append((k, ai, aj, args.nodes, args.jitter,
                      not args.all_positions))
    rng.shuffle(tasks)

    workers = args.workers or os.cpu_count()
    print("%s vs %s, %d pairs at %d nodes, %d workers"
          % (args.engine_a, args.engine_b, len(tasks), args.nodes, workers))

    skipped = 0
    # SIGTERM as well as SIGINT: `kill <pid>` sends SIGTERM, and unhandled it
    # orphans every engine instead of shutting them down.
    def _leave(*_args):
        arena.stop_pool()
        print("\ninterrupted", flush=True)
        os._exit(130)

    for _sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(_sig, _leave)

    def save():
        with open(args.out + ".tmp", "w") as f:
            json.dump({"by_index": {str(k): v for k, v in done.items()},
                       "key": list(key), "skipped": skipped}, f)
        os.replace(args.out + ".tmp", args.out)

    if tasks:
        with mp.Pool(workers, initializer=worker_init,
                     initargs=(args.engine_a, args.engine_b)) as p:
            for i, (k, scores, info) in enumerate(p.imap_unordered(_pair, tasks), 1):
                if scores is None:
                    skipped += 1
                else:
                    done[k] = scores
                if i % 20 == 0 or i == len(tasks):
                    save()
                    print("  %d/%d, %d skipped" % (i, len(tasks), skipped))
        save()

    per_game = [s for k in sorted(done) for s in done[k]]
    if not per_game:
        sys.exit("no games completed")
    print("\n=== %s (A) against %s (B), equal nodes ===" % (args.engine_a,
                                                            args.engine_b))
    # No SPRT here on purpose. A sequential test answers "is this change worth
    # keeping"; this is a magnitude question against a known-stronger
    # reference, so the interval is the answer and a [0,4] verdict would be
    # noise dressed as a decision.
    e, lo, hi, st = stats.elo_with_ci(per_game)
    wins = sum(1 for s in per_game if s == 1.0)
    draws = sum(1 for s in per_game if s == 0.5)
    print("Games:   %d" % st["n"])
    print("Score:   %.4f +/- %.4f" % (st["mean"], 1.96 * st["se"]))
    print("W/D/L:   %d / %d / %d" % (wins, draws, st["n"] - wins - draws))
    print("Elo:     %+.2f  [%+.2f, %+.2f]" % (e, lo, hi))
    if skipped:
        print("%d pairs skipped (position unplayable for one engine)" % skipped)
    save()
    print("\nwrote %s" % args.out)


if __name__ == "__main__":
    main()
