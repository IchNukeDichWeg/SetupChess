"""Paired full-game match: does best-response re-targeting actually pay?

The setup-only screen said no -- re-targeting gives up a forced setup mate
against queen spam -- but that screen scores only the placement phase, which
is exactly the half re-targeting is not trying to win. This plays the WHOLE
game, setup through result, so the chess-phase payoff it is optimising for is
finally on the scoreboard.

Paired by construction: for every (opponent, colour, seed) the same game is
played twice, once with re-targeting off and once on, with identical node
jitter. The statistic is the per-pair difference, which cancels the opponent
and the colour and leaves only the thing being tested. An unpaired comparison
of two independent means would need several times the games for the same
resolution.

Writes only to --out, which has no default.
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
import play
import pool as poolmod
import rules
import stats


def _game(args):
    """One (opponent, colour, seed) played both ways. Returns (a, b) scores."""
    k, ours, theirs, color, nodes, jitter, pool_armies, matrix = args
    engine = arena._worker_engine()
    out = []
    for use_pool in (False, True):
        us = play.Drafter(ours, color,
                          pool=pool_armies if use_pool else None,
                          matrix=matrix if use_pool else None)
        them = play.Drafter(theirs, not color)
        drafters = {color: us, not color: them}
        try:
            state = play.draft(drafters[chess.WHITE], drafters[chess.BLACK])
        except (ValueError, RuntimeError) as e:
            return k, None, str(e)
        if state.result:
            # decided in the placement phase; no chess played
            score = 1.0 if ((state.result == "1-0") == (color == chess.WHITE)) \
                else 0.0
            out.append(score)
            continue
        fen = state.handoff_fen()
        ok, why = rules.engine_safe(fen)
        if not ok:
            return k, None, why
        board = chess.Board(fen)
        limit = chess.engine.Limit(
            nodes=arena.game_nodes(nodes, jitter, k, k + 1, k, 0))
        try:
            while (not board.is_game_over(claim_draw=True)
                   and board.ply() < arena.PLY_LIMIT):
                mv = engine.play(board, limit).move
                if mv is None:
                    break
                board.push(mv)
        except (chess.engine.EngineError,
                chess.engine.EngineTerminatedError) as e:
            arena._ENGINE = None
            return k, None, str(e)
        outcome = board.outcome(claim_draw=True)
        if outcome is None or outcome.winner is None:
            out.append(0.5)
        else:
            out.append(1.0 if outcome.winner == color else 0.0)
    return k, tuple(out), ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", required=True, help="our army JSON or archetype")
    ap.add_argument("--pool", required=True,
                    help="campaign state; the opponent field and the matrix")
    ap.add_argument("--out", required=True, help="results JSON (no default)")
    ap.add_argument("--games", type=int, default=400,
                    help="paired games; each plays the position twice")
    ap.add_argument("--engine", default="stockfish")
    ap.add_argument("--nodes", type=int, default=20000)
    ap.add_argument("--jitter", type=float, default=0.15)
    ap.add_argument("--workers", type=int, default=0, help="0 = all cores")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    ours = play.load_army(args.target)
    pool_armies, matrix = play.load_pool(args.pool)
    # the opponent field is every archetype plus every pool army, so the test
    # is not run against the handful of shapes the champion was bred on
    field = [poolmod.complete(a, random.Random(0))
             for a in poolmod.ARCHETYPES.values()] + list(pool_armies)
    rng = random.Random(args.seed)
    tasks = []
    for k in range(args.games):
        theirs = field[k % len(field)]
        color = chess.WHITE if k % 2 == 0 else chess.BLACK
        tasks.append((k, ours, theirs, color, args.nodes, args.jitter,
                      pool_armies, matrix))
    rng.shuffle(tasks)

    workers = args.workers or os.cpu_count()
    print("%d paired games over a field of %d opponents, %d workers"
          % (len(tasks), len(field), workers))

    plain, retarget, skipped = [], [], 0
    signal.signal(signal.SIGINT, lambda *_: (arena.stop_pool(),
                                             print("\ninterrupted", flush=True),
                                             os._exit(130)))
    with mp.Pool(workers, initializer=arena.worker_init,
                 initargs=(args.engine,)) as p:
        for i, (k, scores, info) in enumerate(
                p.imap_unordered(_game, tasks), 1):
            if scores is None:
                skipped += 1
            else:
                plain.append(scores[0])
                retarget.append(scores[1])
            if i % 25 == 0 or i == len(tasks):
                print("  %d/%d, %d skipped" % (i, len(tasks), skipped))

    if not plain:
        sys.exit("no games completed")
    diffs = [b - a for a, b in zip(plain, retarget)]
    # the paired difference lives in [-1, 1]; map to [0, 1] so the existing
    # score-based SPRT applies, where 0.5 is "no difference"
    mapped = [(d + 1.0) / 2.0 for d in diffs]
    st = stats.score_stats(mapped)

    print("\nre-targeting OFF: %.4f over %d games" % (sum(plain) / len(plain),
                                                      len(plain)))
    print("re-targeting ON : %.4f over %d games" % (sum(retarget) / len(retarget),
                                                    len(retarget)))
    print("\npaired difference (ON minus OFF)")
    print("Pairs:   %d" % st["n"])
    print("Better:  %d   Same: %d   Worse: %d"
          % (sum(1 for d in diffs if d > 0), sum(1 for d in diffs if d == 0),
             sum(1 for d in diffs if d < 0)))
    print("Diff:    %+.4f +/- %.4f" % (sum(diffs) / len(diffs),
                                       1.96 * st["se"] * 2))
    print("Elo:     %+.2f  [%+.2f, %+.2f]"
          % (stats.elo(st["mean"]), stats.elo(max(st["lo"], 1e-9)),
             stats.elo(min(st["hi"], 1 - 1e-9))))
    llr = stats.sprt_llr(mapped)
    print("SPRT:    [0,4] LLR %+.3f -> %s" % (llr, stats.sprt_verdict(llr)))
    if skipped:
        print("%d games skipped (engine could not take the position)" % skipped)

    with open(args.out, "w") as f:
        json.dump({"plain": plain, "retarget": retarget, "diffs": diffs,
                   "nodes": args.nodes, "jitter": args.jitter,
                   "field": len(field), "skipped": skipped,
                   "target": args.target, "pool": args.pool}, f)
    print("\nwrote %s" % args.out)


if __name__ == "__main__":
    main()
