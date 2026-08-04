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
import sys
import time

import arena
import pool as poolmod
import rules
import solve
import stats


def new_state(seed, meta):
    rng = random.Random(seed)
    armies = poolmod.seed_pool(rng)
    return {"meta": meta, "armies": [poolmod.to_json(a) for a in armies],
            "cells": {}, "rounds": [], "seed": seed}


def load_state(path, seed, meta):
    if not os.path.exists(path):
        return new_state(seed, meta)
    with open(path) as f:
        return json.load(f)


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


def run_pairs(tasks, engine_path, workers, cells, label, on_save=None):
    """Play a list of arena tasks into `cells`, checkpointing as they land.

    `cells` is a plain dict rather than the state, because screening runs
    against candidates that may never join the pool and their results must
    not touch the pool matrix (see the round loop).
    """
    if not tasks:
        return 0, 0
    done = errors = unplayable = 0
    start = time.time()
    with mp.Pool(workers, initializer=arena.worker_init,
                 initargs=(engine_path,)) as p:
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
    """Game pairs needed to fill (a, b) and (b, a) for the given indices."""
    out = []
    for i in indices_a:
        for j in indices_b:
            for g in range(pairs):
                for cell in ((i, j, g), (j, i, g)):
                    if cell not in cells:
                        out.append((cell[0], cell[1],
                                    g, (armies[cell[0]], armies[cell[1]]),
                                    nodes, jitter))
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
    args = ap.parse_args()

    workers = args.workers or os.cpu_count()
    meta = {"nodes": args.nodes, "jitter": args.jitter, "pairs": args.pairs,
            "screen_pairs": args.screen_pairs,
            "screen_margin": args.screen_margin,
            "breadth": args.breadth, "crossover_rate": args.crossover_rate,
            "engine": os.path.basename(args.engine),
            "ply_limit": arena.PLY_LIMIT}
    state = load_state(args.state, args.seed, meta)
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

        run_pairs(todo, args.engine, workers, cells_of(state), "seed", persist)

    for rnd in range(len(state["rounds"]), args.rounds):
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

        extra = sorted((i for i in range(len(armies)) if i not in weights),
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

        # screen against the support only: a kill filter, never a measurement
        scratch = dict(cells_of(state))
        tasks = cell_tasks(ext, args.screen_pairs, scratch,
                           new_idx, support, args.nodes, args.jitter)
        run_pairs(tasks, args.engine, workers, scratch, "screen")
        scored = screen_scores(scratch, len(ext), new_idx, weights)
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
        run_pairs(tasks, args.engine, workers, cells_of(state), "fill", persist)

        # prune: keep the equilibrium support plus the best of the rest
        weights2, rep2 = solve_pool(state, args.max_holes)
        if len(armies) > args.max_pool:
            full = arena.matrix_from_cells(cells_of(state), len(armies))
            def mean_score(i):
                vals = [full[i][j] for j in range(len(armies))
                        if j != i and full[i][j] is not None]
                return sum(vals) / len(vals) if vals else -1.0
            ranked = sorted(range(len(armies)),
                            key=lambda i: (i in weights2, mean_score(i)),
                            reverse=True)
            survivors = sorted(ranked[:args.max_pool])
            remap = {old: new for new, old in enumerate(survivors)}
            state["armies"] = [state["armies"][i] for i in survivors]
            put_cells(state, {(remap[i], remap[j], g): v
                              for (i, j, g), v in cells_of(state).items()
                              if i in remap and j in remap})
            weights2 = {remap[i]: w for i, w in weights2.items()
                        if i in remap}
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
    tasks = []
    for k in range(args.final_games):
        ours = armies[frng.choices(mix, weights=mix_w, k=1)[0]]
        theirs = base[frng.randrange(len(base))]
        tasks.append(("g%d" % k, ours, theirs, args.nodes, args.jitter, k))
    print("\n=== gate match: solved mix vs the %d hand-written archetypes ==="
          % len(base))
    print("%d pairs, colours swapped inside each pair" % len(tasks))
    per_game, skipped = [], 0
    with mp.Pool(workers, initializer=arena.worker_init,
                 initargs=(args.engine,)) as p:
        for tag, scores, info in p.imap_unordered(arena.play_match, tasks):
            if scores is None:
                skipped += 1
            else:
                per_game.extend(scores)
    if per_game:
        print(stats.report(per_game, elo0=0.0, elo1=4.0))
    else:
        print("no playable pairs; nothing measured")
    if skipped:
        print("%d pairs skipped: too many pieces for this engine" % skipped)
    with open(args.state + ".gate", "w") as f:
        json.dump({"scores": per_game, "skipped": skipped,
                   "support": {str(k): v for k, v in weights.items()},
                   "meta": state["meta"]}, f)
    print("\nstate: %s" % args.state)


if __name__ == "__main__":
    main()
