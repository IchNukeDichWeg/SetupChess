"""Pool expansion: double oracle over the setup pool.

One round is: solve the current matrix for the equilibrium mix, mutate the
setups the mix actually plays, screen the challengers against that mix, admit
the survivors into the pool and fill their rows, then prune. Repeat until a
round admits nothing.

Why a screen before admission: admitting a challenger costs a full row and
column against the whole pool, which is 2n game pairs and grows with the pool.
Screening costs --screen-pairs against the support only. Per CLAUDE.md
section 5 the screen is a KILL FILTER, not a measurement: its error bars are
far wider than the differences it is sorting, so it only ever throws away
setups that are clearly bad. Nothing is ever reported as an improvement on
screen evidence, and everything admitted gets measured properly afterwards.

Everything lives in one resumable state file given by --state, which has no
default. Ctrl-C is safe: the state is written after every stage of every
round, and a restart re-enters at the same place.
"""

import argparse
import json
import multiprocessing as mp
import os
import random
import signal
import sys
import time

import arena
import pool as poolmod
import rules
import solve
import stats


def new_state(seed, meta, seed_bot=False):
    """Fresh campaign. `seed_bot` adds the bot's wall and PINS it.

    Pinning matters: the prune ranks by equilibrium support and mean score, so
    a fixed opponent that is merely hard to beat rather than good at winning
    can be dropped, and then the campaign quietly stops breeding against it
    with nothing in the log to say so.
    """
    rng = random.Random(seed)
    armies = poolmod.seed_pool(rng)
    pinned = []
    if seed_bot:
        pinned = [len(armies)]
        armies = armies + [poolmod.BOT_WALL]
    return {"meta": meta, "armies": [poolmod.to_json(a) for a in armies],
            "cells": {}, "rounds": [], "seed": seed, "pinned": pinned}


def load_state(path, seed, meta, seed_bot=False):
    if not os.path.exists(path):
        return new_state(seed, meta, seed_bot)
    with open(path) as f:
        state = json.load(f)
    state.setdefault("pinned", [])
    return state


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def cells_of(state):
    return {tuple(int(x) for x in k.split(",")): v
            for k, v in state["cells"].items()}


def put_cells(state, cells):
    state["cells"] = {"%d,%d,%d" % k: v for k, v in cells.items()}


def solve_pool(state, max_holes):
    """Equilibrium over the current pool. Returns (weights_by_index, report)."""
    armies = [poolmod.from_json(a) for a in state["armies"]]
    full = arena.matrix_from_cells(cells_of(state), len(armies))
    m, keep, rep = solve.prepare(full, max_holes)
    if not m:
        raise SystemExit("no setup has a measured row; run more pairs")
    x, value = solve.nash(m)
    weights = {keep[i]: float(x[i]) for i in range(len(keep)) if x[i] > 1e-9}
    rep.update({"value": value, "exploitability": solve.exploitability(m, x)})
    return weights, rep


_CHECKPOINT = None       # set by run_pairs so the signal handler can save


def _on_sigint(*_args):
    """Save, take the engines down, and leave immediately.

    Catching KeyboardInterrupt around the pool loop does not work: the parent
    blocks inside Pool teardown before the except body runs, leaving one
    Stockfish per core alive at 100% CPU (measured). An explicit handler
    plus os._exit avoids Pool.__exit__ entirely.

    os._exit skips multiprocessing's own cleanup, so the resource tracker
    prints a "leaked semaphore objects" warning on the way out. That is
    cosmetic: the semaphores belong to the dying process and the kernel
    reclaims them. Exit 130 is deliberate and correct here -- an interrupted
    run SHOULD stop a chained command. To end the loop early and still have
    the chain continue, use --stop-at-max or --max-minutes, which leave
    through the gate match and exit 0.
    """
    if _CHECKPOINT:
        try:
            _CHECKPOINT()
        except Exception:
            pass
    arena.stop_pool()
    print("\ninterrupted -- progress saved to the last checkpoint; re-run the "
          "same command to resume", flush=True)
    os._exit(130)


def run_pairs(tasks, engine_path, workers, cells, label, on_save=None,
              max_pieces=None):
    """Play a list of arena tasks into `cells`, checkpointing as they land.

    `cells` is a plain dict rather than the state, because screening runs
    against candidates that may never join the pool and their results must
    not touch the pool matrix (see the round loop).
    """
    if not tasks:
        return 0, 0
    global _CHECKPOINT
    _CHECKPOINT = (lambda: on_save(cells)) if on_save else None
    done = errors = unplayable = 0
    start = time.time()
    with mp.Pool(workers, initializer=arena.worker_init,
                 initargs=(engine_path, max_pieces)) as p:
        for i, j, g, score, info in p.imap_unordered(arena.play_cell, tasks):
            done += 1
            if score == "unplayable":
                unplayable += 1
                cells[(i, j, g)] = None
            elif score is None:
                errors += 1
            else:
                cells[(i, j, g)] = score
            if done % 25 == 0 or done == len(tasks):
                if on_save:
                    on_save(cells)
                rate = done / max(1e-9, time.time() - start)
                print("    %s %d/%d, %.2f pairs/s, eta %.1f min"
                      % (label, done, len(tasks), rate,
                         (len(tasks) - done) / max(rate, 1e-9) / 60))
    if on_save:
        on_save(cells)
    return unplayable, errors


def cell_tasks(armies, pairs, cells, indices_a, indices_b, nodes, jitter):
    """Game pairs needed to fill (a, b) and (b, a) for the given indices.

    Deduplicated: when the two index lists overlap, the (i, j) pass and the
    (j, i) pass both reach the same cell, which queued every seeding cell
    twice and doubled the bill for the first phase of a run.
    """
    out = []
    queued = set()
    for i in indices_a:
        for j in indices_b:
            for g in range(pairs):
                for cell in ((i, j, g), (j, i, g)):
                    if cell not in cells and cell not in queued:
                        queued.add(cell)
                        out.append((cell[0], cell[1],
                                    g, (armies[cell[0]], armies[cell[1]]),
                                    nodes, jitter))
    return out


# The screen plays challengers against the equilibrium support. A pinned
# opponent (--seed-bot) is normally NOT in the support: the bot's wall draws
# rather than wins, so the solver gives it no weight, and a challenger that is
# never played against it cannot possibly be selected for beating it. Seeding
# the wall into the pool without this does nothing at all -- measured on a
# 13-army smoke campaign, where the support came out {7, 9} and the pin at 12
# was never once a screen opponent.
#
# So pinned members take a fixed share of the screen weight, independent of the
# equilibrium. Half is a CHOICE, not a measurement: it says answering the real
# opponent counts as much as beating our own pool. Lower it to weight the pool
# more heavily.
PINNED_SCREEN_WEIGHT = 0.5


def screen_weights(weights, pinned):
    """Equilibrium weights, renormalised to leave room for the pinned share."""
    if not pinned:
        return dict(weights)
    total = sum(weights.values()) or 1.0
    out = {i: (1.0 - PINNED_SCREEN_WEIGHT) * w / total
           for i, w in weights.items()}
    share = PINNED_SCREEN_WEIGHT / len(pinned)
    for i in pinned:
        out[i] = out.get(i, 0.0) + share
    return out


def screen_scores(cells, n, challenger_idx, weights):
    """Weighted score of each challenger against the equilibrium mix."""
    full = arena.matrix_from_cells(cells, n)
    out = {}
    for i in challenger_idx:
        num = den = 0.0
        for j, w in weights.items():
            a, b = full[i][j], full[j][i]
            v = None
            if a is not None and b is not None:
                v = (a + (1.0 - b)) / 2.0
            elif a is not None:
                v = a
            elif b is not None:
                v = 1.0 - b
            if v is not None:
                num += w * v
                den += w
        out[i] = (num / den) if den > 0 else None
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", required=True,
                    help="resumable state JSON (no default; not a repo path)")
    ap.add_argument("--engine", default="stockfish")
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--challengers", type=int, default=24,
                    help="mutants generated per round")
    ap.add_argument("--screen-pairs", type=int, default=1,
                    help="pairs per challenger against each support setup")
    ap.add_argument("--screen-margin", type=float, default=0.50,
                    help="screen score a challenger must beat to be admitted")
    ap.add_argument("--pairs", type=int, default=4,
                    help="pairs per cell when filling an admitted row")
    ap.add_argument("--breadth", type=int, default=4,
                    help="extra non-support parents bred from each round")
    ap.add_argument("--crossover-rate", type=float, default=0.35)
    ap.add_argument("--dry-rounds", type=int, default=2,
                    help="consecutive rounds admitting nothing before stopping")
    ap.add_argument("--max-pool", type=int, default=60)
    ap.add_argument("--max-holes", type=int, default=2)
    ap.add_argument("--nodes", type=int, default=20000)
    ap.add_argument("--jitter", type=float, default=0.15)
    ap.add_argument("--workers", type=int, default=0, help="0 = all cores")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--final-games", type=int, default=200,
                    help="pairs in the gate match against the archetypes")
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="leave the round loop after this long and go to the "
                         "gate match; 0 for no limit")
    ap.add_argument("--seed-bot", action="store_true",
                    help="add chess.com's own drafted army (pool.BOT_WALL) to "
                         "the starting pool and PIN it, so every challenger is "
                         "screened against it and the prune cannot drop it. "
                         "Its 24 pieces put most matchups over Stockfish's "
                         "ceiling, so pair this with --engine ./cuci.py "
                         "--max-pieces 0")
    ap.add_argument("--max-pieces", type=int, default=32,
                    help="piece ceiling for the engine; 0 for none, which is "
                         "correct for ./cuci.py (default: %(default)s)")
    ap.add_argument("--stop-at-max", action="store_true",
                    help="leave the round loop once the pool reaches "
                         "--max-pool. Past that point every round prunes and "
                         "re-admits at a cost that grows with the pool, while "
                         "exploitability has been pinned at 0 the whole time, "
                         "so there is no signal left to buy")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _on_sigint)
    workers = args.workers or os.cpu_count()
    meta = {"nodes": args.nodes, "jitter": args.jitter, "pairs": args.pairs,
            "screen_pairs": args.screen_pairs,
            "screen_margin": args.screen_margin,
            "breadth": args.breadth, "crossover_rate": args.crossover_rate,
            "engine": os.path.basename(args.engine),
            "ply_limit": arena.PLY_LIMIT,
            "max_pieces": args.max_pieces, "seed_bot": args.seed_bot}
    state = load_state(args.state, args.seed, meta, args.seed_bot)
    # A campaign written before these keys existed was built at their defaults,
    # so fill them in rather than refusing to resume every state file in git.
    for k, default in (("max_pieces", 32), ("seed_bot", False)):
        state["meta"].setdefault(k, default)
    if state["meta"] != meta:
        sys.exit("state was built with different settings:\n  file: %r\n  now:  %r"
                 % (state["meta"], meta))
    rng = random.Random(args.seed + 1000 * len(state["rounds"]))

    # the seed pool needs a full matrix before the first solve
    armies = [poolmod.from_json(a) for a in state["armies"]]
    idx = list(range(len(armies)))
    todo = cell_tasks(armies, args.pairs, cells_of(state), idx, idx,
                      args.nodes, args.jitter)
    if todo:
        print("seeding: %d pairs over %d setups" % (len(todo), len(armies)))

        def persist(cells):
            put_cells(state, cells)
            save_state(args.state, state)

        run_pairs(todo, args.engine, workers, cells_of(state), "seed", persist,
                  args.max_pieces)

    loop_start = time.time()
    for rnd in range(len(state["rounds"]), args.rounds):
        # Leaving the loop is not failure: the gate match still runs and the
        # process still exits 0, so a chained command carries on.
        if args.max_minutes and (time.time() - loop_start) / 60.0 >= args.max_minutes:
            print("\nreached the %g minute budget after %d rounds; going to "
                  "the gate match" % (args.max_minutes,
                                      rnd - len(state["rounds"])))
            break
        if args.stop_at_max and len(state["armies"]) >= args.max_pool:
            print("\npool is full at %d setups; further rounds only prune and "
                  "re-admit at rising cost, going to the gate match"
                  % len(state["armies"]))
            break
        armies = [poolmod.from_json(a) for a in state["armies"]]
        weights, rep = solve_pool(state, args.max_holes)
        print("\nround %d: pool %d, support %d, value %.4f, exploitability "
              "%.4f" % (rnd, len(armies), len(weights), rep["value"],
                        rep["exploitability"]))

        # challengers are mutations of what the equilibrium actually plays
        support = sorted(weights, key=lambda i: -weights[i])
        # breeding only from the support collapses to one parent whenever the
        # equilibrium is pure, and single edits of one champion cannot leave a
        # local optimum. --breadth adds the next-best setups by mean score as
        # extra parents so crossover has something to recombine with.
        full_now = arena.matrix_from_cells(cells_of(state), len(armies))

        def mean_score(i):
            vals = [full_now[i][j] for j in range(len(armies))
                    if j != i and full_now[i][j] is not None]
            return sum(vals) / len(vals) if vals else -1.0

        # A pinned setup is an OPPONENT MODEL, not one of our candidates, so it
        # is excluded from the parents. Left in, it wins a breadth slot on mean
        # score (a wall that draws everything scores ~0.5 against a field with
        # bad armies in it) and the campaign drifts from "beat the bot" to
        # "become the bot".
        pinned = [i for i in state.get("pinned", []) if i < len(armies)]
        extra = sorted((i for i in range(len(armies))
                        if i not in weights and i not in pinned),
                       key=mean_score, reverse=True)[:args.breadth]
        parent_pool = [armies[i] for i in support + extra]
        seen = {poolmod.army_key(a) for a in armies}
        fresh = []
        for _ in range(args.challengers):
            cand = poolmod.breed(parent_pool, rng, args.crossover_rate)
            if poolmod.army_key(cand) not in seen:
                seen.add(poolmod.army_key(cand))
                fresh.append(cand)
        if not fresh:
            print("  no novel mutants; stopping")
            break
        # challengers live in a scratch index space until they are admitted.
        # Persisting them before the screen would leave every killed mutant
        # in the pool as an unplayed column, punching a hole in every other
        # row until no setup has a measurable one and the solver gives up.
        first = len(armies)
        ext = armies + fresh
        new_idx = list(range(first, len(ext)))
        print("  %d novel challengers" % len(fresh))

        # screen against the support plus any pinned opponents: a kill filter,
        # never a measurement
        scratch = dict(cells_of(state))
        screen_w = screen_weights(weights, pinned)
        opponents = sorted(set(support) | set(pinned))
        tasks = cell_tasks(ext, args.screen_pairs, scratch,
                           new_idx, opponents, args.nodes, args.jitter)
        run_pairs(tasks, args.engine, workers, scratch, "screen",
                  max_pieces=args.max_pieces)
        scored = screen_scores(scratch, len(ext), new_idx, screen_w)
        if pinned:
            print("  screening against %d support + %d pinned (%.0f%% of the "
                  "weight on pinned)" % (len(support), len(pinned),
                                         PINNED_SCREEN_WEIGHT * 100))
        admitted = [i for i in new_idx
                    if scored[i] is not None and scored[i] > args.screen_margin]
        killed = [i for i in new_idx if i not in admitted]
        print("  screen: %d admitted, %d killed (best %.3f)"
              % (len(admitted), len(killed),
                 max((v for v in scored.values() if v is not None),
                     default=float("nan"))))

        if not admitted:
            state["rounds"].append({"round": rnd, "admitted": 0,
                                    "killed": len(killed),
                                    "pool": len(armies),
                                    "value": rep["value"]})
            save_state(args.state, state)
            dry = 0
            for r in reversed(state["rounds"]):
                if r.get("admitted", 0):
                    break
                dry += 1
            print("  nothing survived the screen (%d dry round(s) of %d)"
                  % (dry, args.dry_rounds))
            if dry >= args.dry_rounds:
                print("  the pool has converged")
                break
            continue

        # only survivors join the pool, carrying their screen cells with them
        remap = {old: first + k for k, old in enumerate(sorted(admitted))}
        armies = armies + [ext[i] for i in sorted(admitted)]
        state["armies"].extend(poolmod.to_json(ext[i]) for i in sorted(admitted))
        cells = cells_of(state)
        for (i, j, g), v in scratch.items():
            i2, j2 = remap.get(i, i), remap.get(j, j)
            if i2 < len(armies) and j2 < len(armies) and (i2, j2, g) not in cells:
                cells[(i2, j2, g)] = v
        put_cells(state, cells)
        save_state(args.state, state)
        admitted = sorted(remap.values())

        # admitted challengers get measured properly against the whole pool
        def persist(cells):
            put_cells(state, cells)
            save_state(args.state, state)

        tasks = cell_tasks(armies, args.pairs, cells_of(state), admitted,
                           list(range(len(armies))), args.nodes, args.jitter)
        run_pairs(tasks, args.engine, workers, cells_of(state), "fill", persist,
                  args.max_pieces)

        # prune: keep the equilibrium support plus the best of the rest
        weights2, rep2 = solve_pool(state, args.max_holes)
        if len(armies) > args.max_pool:
            full = arena.matrix_from_cells(cells_of(state), len(armies))
            def mean_score(i):
                vals = [full[i][j] for j in range(len(armies))
                        if j != i and full[i][j] is not None]
                return sum(vals) / len(vals) if vals else -1.0
            ranked = sorted(range(len(armies)),
                            key=lambda i: (i in set(pinned), i in weights2,
                                           mean_score(i)),
                            reverse=True)
            survivors = sorted(ranked[:args.max_pool])
            lost = set(pinned) - set(survivors)
            if lost:
                sys.exit("prune would drop pinned setups %s; raise --max-pool"
                         % sorted(lost))
            remap = {old: new for new, old in enumerate(survivors)}
            state["armies"] = [state["armies"][i] for i in survivors]
            put_cells(state, {(remap[i], remap[j], g): v
                              for (i, j, g), v in cells_of(state).items()
                              if i in remap and j in remap})
            weights2 = {remap[i]: w for i, w in weights2.items()
                        if i in remap}
            state["pinned"] = sorted(remap[i] for i in pinned)
            print("  pruned to %d setups" % len(survivors))

        state["rounds"].append({
            "round": rnd, "admitted": len(admitted), "killed": len(killed),
            "pool": len(state["armies"]), "value": rep2["value"],
            "exploitability": rep2["exploitability"],
            "support": {str(k): v for k, v in sorted(weights2.items())}})
        save_state(args.state, state)
        print("  round %d done: pool %d, support %d, exploitability %.4f"
              % (rnd, len(state["armies"]), len(weights2),
                 rep2["exploitability"]))

    # Gate 4: the solved mix against the hand-written archetypes, played as a
    # real match. Re-using matrix cells would report a tight CI over games
    # that were never played, and the same cells chose the mix in the first
    # place, so the mix would be scored on its own training data.
    weights, rep = solve_pool(state, args.max_holes)
    if args.final_games <= 0:
        print("\nskipping the gate match (--final-games 0); state: %s"
              % args.state)
        return
    armies = [poolmod.from_json(a) for a in state["armies"]]
    base = poolmod.seed_pool(random.Random(state["seed"]))
    mix = sorted(weights)
    mix_w = [weights[i] for i in mix]
    frng = random.Random(args.seed + 99991)

    # Resumable, keyed by game index: a 1,000-pair match is an hour of work
    # and losing it to one dead worker is not acceptable. Game k is fully
    # determined by (k, seed, mix), so a re-run replays nothing.
    gate_path = args.state + ".gate"
    gate = {}
    if os.path.exists(gate_path):
        with open(gate_path) as f:
            prev = json.load(f)
        gate = {int(k): v for k, v in prev.get("by_index", {}).items()}
        if gate:
            print("\nresuming the gate match: %d pairs already recorded (%d "
                  "playable)" % (len(gate), sum(1 for v in gate.values() if v)))

    tasks = []
    for k in range(args.final_games):
        ours = armies[frng.choices(mix, weights=mix_w, k=1)[0]]
        theirs = base[frng.randrange(len(base))]
        if k in gate:
            continue
        tasks.append(("g%d" % k, ours, theirs, args.nodes, args.jitter, k))
    print("\n=== gate match: solved mix vs the %d hand-written archetypes ==="
          % len(base))
    print("%d pairs to play, colours swapped inside each pair" % len(tasks))

    def save_gate():
        with open(gate_path + ".tmp", "w") as f:
            json.dump({"by_index": {str(k): v for k, v in gate.items()},
                       "support": {str(k): v for k, v in weights.items()},
                       "meta": state["meta"]}, f)
        os.replace(gate_path + ".tmp", gate_path)

    skipped = 0
    if tasks:
        start = time.time()
        done = 0
        with mp.Pool(workers, initializer=arena.worker_init,
                     initargs=(args.engine, args.max_pieces)) as p:
            it = p.imap_unordered(arena.play_match, tasks)
            while done < len(tasks):
                try:
                    # a worker that dies takes its result with it and the
                    # iterator would otherwise block forever: 8 of 10 workers
                    # died once and the run sat silent for 23 minutes. A
                    # timeout turns that hang into a reported failure.
                    tag, scores, info = it.next(timeout=600)
                except mp.TimeoutError:
                    print("  no result for 10 minutes -- workers have died; "
                          "%d/%d pairs are saved, re-run to finish"
                          % (done, len(tasks)), flush=True)
                    arena.stop_pool()
                    break
                done += 1
                if scores is None:
                    # record the skip too, or an unplayable pair is retried on
                    # every resume forever
                    skipped += 1
                    gate[int(tag[1:])] = None
                else:
                    gate[int(tag[1:])] = list(scores)
                if done % 20 == 0 or done == len(tasks):
                    save_gate()
                    rate = done / max(1e-9, time.time() - start)
                    print("    gate %d/%d, %.2f pairs/s, eta %.1f min, "
                          "%d skipped" % (done, len(tasks), rate,
                                          (len(tasks) - done) / max(rate, 1e-9)
                                          / 60, skipped), flush=True)
        save_gate()

    per_game = [s for k in sorted(gate) if gate[k] for s in gate[k]]
    if per_game:
        print(stats.report(per_game, elo0=0.0, elo1=4.0))
    else:
        print("no playable pairs; nothing measured")
    if skipped:
        print("%d pairs skipped: too many pieces for this engine" % skipped)
    save_gate()
    print("\nstate: %s" % args.state)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:   # before the handler is installed
        # the state is written every 25 pairs, so the run resumes from the
        # last checkpoint; say so instead of dumping a multiprocessing stack
        # flush explicitly: the pool teardown can take the interpreter down
        # before buffered stdout reaches a redirected log
        print("\ninterrupted -- progress up to the last checkpoint is saved; "
              "re-run the same command to resume", flush=True)
        sys.exit(130)
