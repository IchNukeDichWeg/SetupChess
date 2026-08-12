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

Resumable: --out doubles as the state file. Game k is fully determined by
(k, field, seed), so raising --games replays nothing and only plays the new
indices. That matters here because most pairs come out identical -- the
drafter only re-targets in some games -- so the effective sample grows much
more slowly than the game count and a run usually needs extending.

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
    """One (opponent, colour, seed) played both ways. Returns (a, b) scores.

    `test` names the toggle under test. "retarget" is the original arm pair --
    pool withheld versus pool given. "optionality" gives BOTH arms the pool and
    varies only whether the drafter places to stay uncommitted, which is the
    right isolation: re-targeting and optionality both read the pool, so an arm
    without it would be testing two changes at once.
    """
    (k, ours, theirs, color, nodes, jitter, pool_armies, matrix, test) = args
    engine = arena._worker_engine()
    out = []
    for on in (False, True):
        if test == "optionality":
            us = play.Drafter(ours, color, pool=pool_armies, matrix=matrix,
                              optionality=on)
        else:
            us = play.Drafter(ours, color,
                              pool=pool_armies if on else None,
                              matrix=matrix if on else None)
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
        ok, why = rules.engine_safe(fen, arena.MAX_PIECES)
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
    ap.add_argument("--test", choices=("retarget", "optionality"),
                    default="retarget",
                    help="which toggle to A/B (default: %(default)s)")
    ap.add_argument("--max-pieces", type=int, default=32,
                    help="piece ceiling for the engine; 0 for none, which is "
                         "correct for fairy-stockfish and ./cuci.py. Judging a "
                         "no-ceiling engine at 32 does not just lose games, it "
                         "loses the HIGH-PIECE-COUNT ones, which biases the "
                         "sample toward exactly the matchups the pool is not "
                         "about (default: %(default)s)")
    ap.add_argument("--workers", type=int, default=0, help="0 = all cores")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    done = {}
    if os.path.exists(args.out):
        with open(args.out) as f:
            prev = json.load(f)
        if (prev.get("nodes"), prev.get("jitter"), prev.get("target"),
                prev.get("pool")) != (args.nodes, args.jitter, args.target,
                                      args.pool):
            sys.exit("existing %s was made with different settings" % args.out)
        done = {int(k): v for k, v in prev.get("by_index", {}).items()}
        print("resuming: %d games already played" % len(done))

    ours = play.load_army(args.target)
    pool_armies, matrix = play.load_pool(args.pool)
    # the opponent field is every archetype plus every pool army, so the test
    # is not run against the handful of shapes the champion was bred on
    field = [poolmod.complete(a, random.Random(0))
             for a in poolmod.ARCHETYPES.values()] + list(pool_armies)
    rng = random.Random(args.seed)
    tasks = []
    for k in range(args.games):
        if k in done:
            continue
        theirs = field[k % len(field)]
        # OPPONENT AND COLOUR MUST NOT SHARE A PERIOD. With `k % 2` the two
        # cycles lock whenever len(field) is even, which it is for every
        # shipped campaign: each opponent is then played from exactly ONE
        # colour for the whole run. Enumerated, 0 of 106 opponents on the v3
        # field and 0 of 84 on v6 were ever seen from both sides. The drafters
        # carry no RNG, so k and k+len(field) draft the identical handoff FEN,
        # and a 3,000-game run sampled 106 distinct positions replayed 28 times
        # instead of 212. Advancing the colour once per full pass decouples
        # them for any field size.
        color = chess.WHITE if (k // len(field)) % 2 == 0 else chess.BLACK
        tasks.append((k, ours, theirs, color, args.nodes, args.jitter,
                      pool_armies, matrix, args.test))
    rng.shuffle(tasks)

    workers = args.workers or os.cpu_count()
    print("%d paired games over a field of %d opponents, %d workers"
          % (len(tasks), len(field), workers))

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
                       "nodes": args.nodes, "jitter": args.jitter,
                       "field": len(field), "skipped": skipped,
                       "target": args.target, "pool": args.pool}, f)
        os.replace(args.out + ".tmp", args.out)

    if tasks:
        arena.preflight_engine(args.engine)
        with mp.Pool(workers, initializer=arena.worker_init,
                     initargs=(args.engine, args.max_pieces)) as p:
            for i, (k, scores, info) in enumerate(
                    p.imap_unordered(_game, tasks), 1):
                if scores is None:
                    skipped += 1
                else:
                    done[k] = list(scores)
                if i % 25 == 0 or i == len(tasks):
                    save()
                    print("  %d/%d, %d skipped" % (i, len(tasks), skipped))
        save()

    order = sorted(done)
    plain = [done[k][0] for k in order]
    retarget = [done[k][1] for k in order]
    if not plain:
        sys.exit("no games completed")
    diffs = [b - a for a, b in zip(plain, retarget)]
    # the paired difference lives in [-1, 1]; map to [0, 1] so the existing
    # score-based SPRT applies, where 0.5 is "no difference"
    mapped = [(d + 1.0) / 2.0 for d in diffs]
    st = stats.score_stats(mapped)

    label = args.test
    print("\n%s OFF: %.4f over %d games"
          % (label, sum(plain) / len(plain), len(plain)))
    print("%s ON : %.4f over %d games"
          % (label, sum(retarget) / len(retarget), len(retarget)))
    print("\npaired difference (ON minus OFF)")
    print("Pairs:   %d" % st["n"])
    print("Better:  %d   Same: %d   Worse: %d"
          % (sum(1 for d in diffs if d > 0), sum(1 for d in diffs if d == 0),
             sum(1 for d in diffs if d < 0)))
    print("Diff:    %+.4f +/- %.4f" % (sum(diffs) / len(diffs),
                                       1.96 * st["se"] * 2))
    # THE AXIS HAS TO BE NAMED. `mapped` has mean 0.5 + (ON - OFF)/2, so this
    # Elo is of a HALF-sized edge, not the gap between the arms: measured over
    # three simulated arm pairs at 400k games each, printed/arm-vs-arm came out
    # 0.495, 0.500, 0.499. Both quantities are legitimate, but the repo quotes
    # these outputs next to field-Elo figures, so printing one unlabelled
    # number understates every A/B here by about 2x. The SPRT band inherits the
    # same factor: [0,4] paired is about [0,8] between the arms.
    print("Elo:     %+.2f  [%+.2f, %+.2f]   (PAIRED half-scale)"
          % (stats.elo(st["mean"]), stats.elo(max(st["lo"], 1e-9)),
             stats.elo(min(st["hi"], 1 - 1e-9))))
    print("Arm Elo: %+.2f  [%+.2f, %+.2f]   (ON minus OFF; derived, x2)"
          % (2 * stats.elo(st["mean"]),
             2 * stats.elo(max(st["lo"], 1e-9)),
             2 * stats.elo(min(st["hi"], 1 - 1e-9))))
    llr = stats.sprt_llr(mapped)
    print("SPRT:    [0,4] paired == [0,8] arm-vs-arm, LLR %+.3f -> %s"
          % (llr, stats.sprt_verdict(llr)))
    if skipped:
        print("%d games skipped (engine could not take the position)" % skipped)

    save()
    print("\nwrote %s" % args.out)


if __name__ == "__main__":
    main()
