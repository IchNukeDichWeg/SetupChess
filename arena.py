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
import atexit
import json
import multiprocessing as mp
import os
import random
import signal
import sys
import time

import chess
import chess.engine

import pool as poolmod
import draft
import rules

# 300 plies is well past where a decided Setup Chess game resolves; a game
# still running at that point is scored as a draw rather than left hanging.
PLY_LIMIT = 300

_ENGINE = None
_ENGINE_PATH = None
# ('white mode', 'black mode', depth, width), or None for the stamped-FEN
# model. See draft.py: None means the two armies never react to each other.
_DRAFT = None
# Piece ceiling for whichever engine the workers are running. Stockfish's by
# default; our own core takes 0 (no ceiling), which is the whole point of it.
MAX_PIECES = rules.ENGINE_MAX_PIECES


class Unplayable(Exception):
    """The position is legal Setup Chess but not safe for this engine."""


def _kill_engine(*_args):
    """Take the engine down with the worker.

    A worker blocked in engine.play() does not notice its parent dying, so
    without this an interrupted campaign leaves one Stockfish per core
    running at 100% with no parent (observed: 8 orphans after signalling a
    run's parent process alone). SIGINT from a terminal reaches the whole
    process group and would be fine; a signal sent to the parent only is not.
    """
    global _ENGINE
    eng, _ENGINE = _ENGINE, None
    if eng is not None:
        try:
            eng.close()
        except Exception:
            pass
    os._exit(0)


def preflight_engine(*paths):
    """Open each engine once in the PARENT and fail loudly if it will not run.

    mp.Pool repopulates any worker that dies in its initializer, forever, and
    worker_init has no error handling: a mistyped --engine printed 20,000
    traceback lines in 25 seconds and never exited. Every harness that builds a
    Pool should call this first, so a typo is a one-line error instead of a
    spin that only Ctrl-C ends. duel.py and match.py inherit the same hazard.
    """
    for path in paths:
        if not path:
            continue
        try:
            eng = chess.engine.SimpleEngine.popen_uci(path)
        except Exception as e:
            sys.exit("cannot start engine %r: %s\n"
                     "(a Pool respawns workers that die in their initializer "
                     "forever, so this is refused up front)" % (path, e))
        try:
            eng.quit()
        except Exception:
            pass


def worker_init(engine_path, max_pieces=None, draft_cfg=None):
    global _ENGINE, _ENGINE_PATH, MAX_PIECES, _DRAFT
    _DRAFT = draft_cfg
    if draft_cfg:
        # Visible, set from an explicit --army-scale rather than inherited from
        # anywhere: this is the one free parameter in the placement eval and it
        # has to be A/B-able to stop being a guess.
        import psearch
        psearch.ARMY_SCALE = draft_cfg[4]
        psearch.PST_SCALE = draft_cfg[5]
    if max_pieces is not None:
        MAX_PIECES = max_pieces
    _ENGINE_PATH = engine_path
    _ENGINE = chess.engine.SimpleEngine.popen_uci(engine_path)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _kill_engine)
    atexit.register(_kill_engine)


def _drop_engine():
    """Close the worker engine before forgetting it.

    EngineTerminatedError means the process is already gone, but a plain
    EngineError does NOT: python-chess raises it from its own protocol layer
    and never touches the subprocess. Setting _ENGINE = None then leaked one
    live engine per error, each holding a thread, an event loop and three pipe
    fds, for the whole lifetime of a pool worker -- which for a campaign is the
    whole campaign. Measured: three EngineErrors in one process took the child
    count from 1 to 3.
    """
    global _ENGINE
    eng, _ENGINE = _ENGINE, None
    if eng is not None:
        try:
            eng.close()
        except Exception:
            pass


def _worker_engine():
    """Restart the engine if it died mid-campaign rather than losing the run."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = chess.engine.SimpleEngine.popen_uci(_ENGINE_PATH)
    return _ENGINE


# Set by main() once the results dict exists, so the signal handler can save.
_CHECKPOINT = None


def _on_signal(*_args):
    """Checkpoint, take the engines down, and leave immediately.

    Registered for SIGTERM as well as SIGINT, and the SIGTERM half is the point.
    `kill <pid>` sends SIGTERM, and with no handler the default action kills the
    parent instantly: nothing is checkpointed and every worker plus its engine
    is orphaned onto init. That is exactly how two fairy-stockfish processes
    were left running on this machine after a stalled campaign was killed --
    the leak was never in stop_pool(), which was measured leaving zero orphans
    when it actually got to run.

    os._exit skips multiprocessing's cleanup, which is deliberate: Pool
    teardown can block forever on a worker stuck in engine I/O. The
    "leaked semaphore objects" warning that follows is the cost of that and is
    not an error.
    """
    if _CHECKPOINT:
        try:
            _CHECKPOINT()
        except Exception:
            pass
    stop_pool()
    print("\ninterrupted -- progress saved to the last checkpoint; re-run the "
          "same command to resume.\nA \"leaked semaphore objects\" warning may "
          "follow: it is expected and nothing was lost.", flush=True)
    os._exit(130)


def stop_pool():
    """Bring workers down so their engines go with them.

    Pool.terminate() joins handler threads that can block forever when a
    worker is stuck in blocking engine I/O, which leaves the parent hung and
    one Stockfish per core running (measured: parent alive at 0% CPU with 4
    orphans 50 seconds after the signal). SIGTERM reaches each worker's
    _kill_engine handler, which closes its engine first.
    """
    for w in mp.active_children():
        w.terminate()
    deadline = time.time() + 3.0
    while time.time() < deadline and mp.active_children():
        time.sleep(0.05)
    for w in mp.active_children():
        w.kill()


def game_nodes(base, jitter, i, j, g, color):
    """Deterministic per-game node count. Same inputs -> same game, always."""
    if jitter <= 0:
        return base
    rng = random.Random((i, j, g, color).__hash__())
    return max(1, int(base * (1.0 + rng.uniform(-jitter, jitter))))


def play_game(white_army, black_army, engine, nodes):
    """Returns White's score, the ply count, and whether it was truncated.

    A game stopped at PLY_LIMIT scores 0.5, which is indistinguishable from a
    real draw once it reaches the matrix. That is a reasonable way to score an
    unfinished game and a terrible thing to leave unreported, so the truncation
    is counted and printed at the end of a run.
    """
    if _DRAFT is None:
        fen = rules.setup_fen(white_army, black_army)
    else:
        # The armies REACT to each other instead of being stamped onto a board,
        # so the position is not known until the phase has been played -- and
        # the setup itself can end the game before any engine move.
        wm, bm, depth, width, _scale, _pst = _DRAFT
        fen, result = draft.handoff(white_army, black_army, wm, bm, depth, width)
        if result is not None:
            score = {"1-0": 1.0, "0-1": 0.0}.get(result, 0.5)
            return score, 0, False
    ok, why = rules.engine_safe(fen, MAX_PIECES)
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
    cut = outcome is None
    if outcome is None or outcome.winner is None:
        return 0.5, board.ply(), cut
    return (1.0 if outcome.winner == chess.WHITE else 0.0), board.ply(), cut


def play_pair(ai, aj, nodes, jitter, i, j, g):
    """Both colours of one matchup. Returns the two scores from ai's side.

    Raises Unplayable before any game starts, so a cell that the engine
    cannot take is recorded once rather than half-played.
    """
    if _DRAFT is None:
        for w, b in ((ai, aj), (aj, ai)):
            ok, why = rules.engine_safe(rules.setup_fen(w, b), MAX_PIECES)
            if not ok:
                raise Unplayable(why)
    engine = _worker_engine()
    w, ply_w, cut_w = play_game(ai, aj, engine,
                                game_nodes(nodes, jitter, i, j, g, 0))
    b, ply_b, cut_b = play_game(aj, ai, engine,
                                game_nodes(nodes, jitter, i, j, g, 1))
    return (w, 1.0 - b), int(cut_w) + int(cut_b)


def play_cell(task):
    """One game pair for cell (i, j): i as White, then i as Black."""
    i, j, g, armies, nodes, jitter = task
    ai, aj = armies
    try:
        (w, b), truncated = play_pair(ai, aj, nodes, jitter, i, j, g)
    except Unplayable as e:
        return i, j, g, "unplayable", str(e)
    except (chess.engine.EngineError, chess.engine.EngineTerminatedError) as e:
        _drop_engine()
        return i, j, g, None, str(e)
    # both games scored from i's perspective, then averaged
    return i, j, g, (w + b) / 2.0, truncated


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
        _drop_engine()
        return tag, None, str(e)
    return tag, list(scores), ""


def load_state(path):
    """(cells, stored_meta). `stored_meta` is {} for a file that predates it."""
    if not os.path.exists(path):
        return {}, {}
    with open(path) as f:
        state = json.load(f)
    if "cells" not in state:
        sys.exit("%s has no 'cells' key, so it is not an arena results file. "
                 "Refusing to overwrite it -- pointing --out at a campaign "
                 "state would destroy its armies, rounds and seed." % path)
    return ({tuple(int(x) for x in k.split(",")): v
             for k, v in state["cells"].items()},
            state.get("meta", {}))


# Settings that change WHAT WAS PLAYED. A resume that differs on any of these
# pools two instruments into one matrix, which is the thing CLAUDE.md section 5
# forbids outright, and save_state then rewrites meta so the file claims every
# cell came from the newest run. expand.py has had this guard all along.
#
# `armies` is in here because index identity is not implied by anything else:
# seed_pool tops up from the rng, so a different --seed puts DIFFERENT armies
# at the same index and every stored cell silently changes meaning.
#
# max_pieces is deliberately NOT in here. Raising it is the documented
# workflow for ./cuci.py, it changes which cells are playable rather than how
# a game is played, and the cells it unlocks are exactly the ones recorded as
# unplayable. Those get retried instead of blocking the resume.
_RESUME_KEYS = ("n", "nodes", "jitter", "pairs", "engine", "seed",
                "ply_limit", "armies", "draft")


def check_resume(stored, current, cells):
    """Refuse a resume that would pool instruments; retry cells a wider
    ceiling has just unlocked. Returns the cells to keep."""
    if not stored:
        return cells
    for k in _RESUME_KEYS:
        if k in stored and stored[k] != current[k]:
            sys.exit("this results file was built with %s=%r and you asked "
                     "for %r. Resuming would average two different "
                     "instruments into one matrix; use a new --out."
                     % (k, stored[k], current[k]))
    missing = [k for k in _RESUME_KEYS if k not in stored]
    if missing:
        print("  note: this file predates the resume guard and carries no %s, "
              "so those could not be verified" % ", ".join(missing))
    if stored.get("max_pieces") != current["max_pieces"]:
        freed = [k for k, v in cells.items() if v is None]
        for k in freed:
            del cells[k]
        print("  --max-pieces changed %r -> %r: %d unplayable cell(s) cleared "
              "for a retry" % (stored.get("max_pieces"),
                               current["max_pieces"], len(freed)))
    return cells


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
    ap.add_argument("--draft", metavar="WHITE:BLACK",
                    help="play the PLACEMENT phase instead of stamping two "
                         "finished armies onto a board, e.g. 'search:plan'. "
                         "Modes: %s. Without this every cell measures armies "
                         "that never reacted to each other (see draft.py)."
                         % "/".join(draft.MODES))
    ap.add_argument("--draft-depth", type=int, default=2,
                    help="placement search depth for --draft (default 2)")
    ap.add_argument("--draft-width", type=int, default=8,
                    help="placement search beam width for --draft (default 8)")
    ap.add_argument("--pst-scale", type=float, default=None,
                    help="weight on the engine's piece-square knowledge in the "
                         "placement eval (default psearch.PST_SCALE, 0=off). "
                         "Material is cancelled exactly, so this is positional "
                         "only.")
    ap.add_argument("--army-scale", type=float, default=None,
                    help="centipawns per unit of equilibrium agreement in the "
                         "placement eval (default psearch.ARMY_SCALE). Trades "
                         "off building a measured-good army against not "
                         "hanging material; UNCALIBRATED, so A/B it.")
    ap.add_argument("--max-pieces", type=int, default=rules.ENGINE_MAX_PIECES,
                    help="piece ceiling for the engine; 0 for none, which is "
                         "correct for ./cuci.py (default: %(default)s)")
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

    preflight_engine(args.engine)
    cells, stored = load_state(args.out)
    global _DRAFT
    if args.draft:
        parts = args.draft.split(":")
        if len(parts) != 2 or any(m not in draft.MODES for m in parts):
            sys.exit("--draft wants WHITE:BLACK with modes from %s, got %r"
                     % ("/".join(draft.MODES), args.draft))
        import psearch
        scale = psearch.ARMY_SCALE if args.army_scale is None else args.army_scale
        pst = psearch.PST_SCALE if args.pst_scale is None else args.pst_scale
        _DRAFT = (parts[0], parts[1], args.draft_depth, args.draft_width,
                  scale, pst)
        print("drafting the placement phase: White=%s Black=%s (depth %d, "
              "width %d, army-scale %g, pst-scale %g)"
              % (parts[0], parts[1], args.draft_depth, args.draft_width,
                 scale, pst))
        if parts[0] != parts[1]:
            # play_pair swaps the ARMIES between the two games of a pair, not
            # the modes, so with different modes the second game is not a
            # colour swap of the first -- it is a different matchup. The
            # antisymmetry diagnostic below is meaningless then (measured:
            # search:plan gave P[i][j]+P[j][i] = 1.083 where plan:plan gave
            # exactly 1.000), and the matrix cannot be antisymmetrised.
            print("  WARNING: modes differ, so a colour-swapped pair also "
                  "swaps strategies. The P[i][j]+P[j][i] check below does NOT "
                  "apply and this matrix must not be antisymmetrised. Use the "
                  "same mode on both sides to measure armies.")

    meta = {"n": n, "nodes": args.nodes, "jitter": args.jitter,
            "pairs": args.pairs, "engine": os.path.basename(args.engine),
            "seed": args.seed, "ply_limit": PLY_LIMIT,
            "max_pieces": args.max_pieces,
            # in _RESUME_KEYS: a drafted cell and a stamped cell measure
            # different games and must never pool into one matrix
            "draft": list(_DRAFT) if _DRAFT else None,
            "armies": [poolmod.fingerprint(a) for a in armies]}
    cells = check_resume(stored, meta, cells)
    tasks = [(i, j, g, (armies[i], armies[j]), args.nodes, args.jitter)
             for i in range(n) for j in range(n)
             for g in range(args.pairs) if (i, j, g) not in cells]
    workers = args.workers or os.cpu_count()

    global _CHECKPOINT
    _CHECKPOINT = lambda: save_state(args.out, cells, meta)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _on_signal)

    print("%d setups, %d cells, %d game pairs to play (%d already done), "
          "%d workers" % (n, n * n, len(tasks), len(cells), workers))
    if not tasks:
        print("nothing to do")
        return

    start = time.time()
    done = errors = unplayable = truncated = 0
    reasons = {}
    with mp.Pool(workers, initializer=worker_init,
                 initargs=(args.engine, args.max_pieces, _DRAFT)) as p:
        for i, j, g, score, info in p.imap_unordered(play_cell, tasks):
            done += 1
            if score == "unplayable":
                unplayable += 1
                # keep the real reason: "too many pieces" and "the non-mover
                # is already in check" are different problems and only the
                # first one is fixed by a different engine
                tag = "piece count" if "pieces exceeds" in (info or "") \
                    else (info or "unknown")
                reasons[tag] = reasons.get(tag, 0) + 1
                cells[(i, j, g)] = None
            elif score is None:
                errors += 1
                print("  engine error on (%d,%d,%d): %s" % (i, j, g, info))
            else:
                cells[(i, j, g)] = score
                truncated += info or 0
            if done % 25 == 0 or done == len(tasks):
                save_state(args.out, cells, meta)
                rate = done / max(1e-9, time.time() - start)
                # flush: stdout is BLOCK buffered when redirected to a file,
                # so a run logged with `> log` showed nothing for many minutes
                # and looked wedged. It was progressing the whole time; the
                # lines were sitting in an 8KB buffer. expand.py already had
                # this fix and arena did not.
                print("  %d/%d pairs, %.2f pairs/s, eta %.1f min, %d errors"
                      % (done, len(tasks), rate,
                         (len(tasks) - done) / max(rate, 1e-9) / 60, errors),
                      flush=True)
    save_state(args.out, cells, meta)

    m = matrix_from_cells(cells, n)
    diag = [m[i][i] for i in range(n) if m[i][i] is not None]
    # i != j, or the DIAGONAL contributes 2*m[i][i] and owns the extremes:
    # a self-play cell is 0.5 by construction, so including it makes the gate
    # report the thing it is not testing. Measured on
    # campaigns/matrix_own_core.json the gate printed max 1.312 where the true
    # off-diagonal max is 1.188.
    sym = [m[i][j] + m[j][i] for i in range(n) for j in range(n)
           if i != j and m[i][j] is not None and m[j][i] is not None]
    print("wrote %s" % args.out)
    if diag:
        print("P[i][i] mean %.4f (want 0.50), min %.3f max %.3f"
              % (sum(diag) / len(diag), min(diag), max(diag)))
    if sym:
        print("P[i][j]+P[j][i] mean %.4f (want 1.00), min %.3f max %.3f"
              % (sum(sym) / len(sym), min(sym), max(sym)))
    # A referee that cannot separate these armies produces a matrix that LOOKS
    # complete -- right cell count, no errors, symmetry checks passing -- and
    # says nothing at all. That is exactly what wasted the v5 campaign, and
    # expand.py grew screen_blind() for it while arena did not: a screen here
    # returned 0.5000 +/- 0.0000 at three different eval weights and read as
    # "no difference" rather than "no information".
    measured = [v for v in cells.values() if v is not None]
    if measured and len(set(measured)) == 1:
        print("WARNING: every measured cell is exactly %.4f. This matrix "
              "carries NO information -- the referee never separated these "
              "armies. A stronger engine or more nodes is needed; do not read "
              "a verdict off it." % measured[0])
    elif len(measured) > 8:
        spread = max(measured) - min(measured)
        if spread < 0.02:
            print("WARNING: all %d measured cells lie within %.4f of each "
                  "other. The referee is barely separating these armies; "
                  "treat any verdict as a kill filter at best."
                  % (len(measured), spread))

    if truncated:
        # A truncated game scores 0.5, so a high rate means the draws in this
        # matrix are partly the ply limit rather than chess.
        played = 2 * (len(tasks) - unplayable)
        print("%d of %d games hit the %d-ply limit (%.1f%%) and were scored as "
              "draws" % (truncated, played, PLY_LIMIT,
                         100.0 * truncated / max(played, 1)))
    else:
        print("no game hit the %d-ply limit" % PLY_LIMIT)
    if unplayable:
        print("%d game pairs skipped:" % unplayable)
        for tag, n_ in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print("   %5d  %s" % (n_, tag))
    if errors:
        print("%d game pairs failed and were not recorded; re-run to fill them"
              % errors)


if __name__ == "__main__":
    main()
