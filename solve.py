"""Solve the payoff matrix: equilibrium mix, best response, exploitability.

Two jobs, one matrix:

* `nash()` -- the unexploitable mix over the pool, by linear programming. For
  a symmetric zero-sum game the value is 0.5 by construction, so the useful
  output is the support: which setups the equilibrium actually plays and how
  often.
* `best_response()` -- the highest-scoring single setup against a known
  opponent distribution. This is the online path, since placement on
  chess.com is alternating and visible (docs/RULES.md).

Holes matter here. The arena leaves a cell empty when the engine cannot be
trusted with the position (rules.ENGINE_MAX_PIECES), so a row can be mostly
unmeasured. An unmeasured cell is NOT a draw, and quietly filling it with 0.5
would let a setup nobody could measure walk into the equilibrium support. Any
setup missing more than --max-holes of its row is dropped outright and named
in the output; whatever remains is filled with 0.5 and counted, so the number
is on screen rather than hidden.
"""

import argparse
import json

import numpy as np
from scipy.optimize import linprog


def nash(matrix):
    """Row player's equilibrium mix for a zero-sum matrix. Returns (x, value).

    maximise v subject to  sum_i x_i * P[i][j] >= v for every column j,
    sum_i x_i == 1, x >= 0. Variables are [x_0 .. x_n-1, v].
    """
    p = np.asarray(matrix, dtype=float)
    n = p.shape[0]
    # -sum_i x_i P[i][j] + v <= 0
    a_ub = np.hstack([-p.T, np.ones((p.shape[1], 1))])
    b_ub = np.zeros(p.shape[1])
    a_eq = np.hstack([np.ones((1, n)), np.zeros((1, 1))])
    b_eq = np.array([1.0])
    c = np.zeros(n + 1)
    c[-1] = -1.0  # maximise v
    bounds = [(0.0, None)] * n + [(None, None)]
    res = linprog(c, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq,
                  bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError("LP failed: %s" % res.message)
    x = np.clip(res.x[:n], 0.0, None)
    return x / x.sum(), float(res.x[-1])


def best_response(matrix, opponent):
    """Best single row against an opponent mix. Returns (index, value)."""
    p = np.asarray(matrix, dtype=float)
    scores = p @ np.asarray(opponent, dtype=float)
    i = int(np.argmax(scores))
    return i, float(scores[i])


def exploitability(matrix, x, value=None):
    """How far below the equilibrium value a best-responding opponent pushes.

    0.0 means unexploitable within the pool; higher is worse.

    `value` defaults to 0.5, which is right for the antisymmetrised matrix the
    default path builds and where this is therefore identically zero. It is
    NOT right for a raw matrix: on [[0.8, 0.3], [0.2, 0.9]] the game value is
    0.55 and hardcoding 0.5 returned -0.0500, a negative exploitability. Pass
    the solved value when the matrix is not antisymmetric.
    """
    p = np.asarray(matrix, dtype=float)
    guaranteed = float(np.min(np.asarray(x, dtype=float) @ p))
    return (0.5 if value is None else value) - guaranteed


def antisymmetrize(full):
    """Force P[i][j] + P[j][i] == 1 using both measurements of a matchup.

    Setup Chess is symmetric: the same two armies, colours rotated. The
    arena measures each direction separately, so noise leaves the raw matrix
    slightly off (1.0106 on the 12-archetype run) and the LP happily
    exploits that asymmetry, reporting an equilibrium value above 0.5 for a
    game whose value is 0.5 by construction. Averaging the two directions
    halves the variance and puts the value back where it belongs. The raw
    matrix is what arena.py reports the symmetry gate on; this is only for
    solving.
    """
    n = len(full)
    out = [[None] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            a, b = full[i][j], full[j][i]
            if a is not None and b is not None:
                out[i][j] = (a + (1.0 - b)) / 2.0
            elif a is not None:
                out[i][j] = a
            elif b is not None:
                out[i][j] = 1.0 - b
    return out


def prepare(full, max_holes, symmetric=True):
    """Antisymmetrise, drop hole-ridden rows, impute the rest.

    Returns (matrix, kept indices, report). Split out from load_matrix so
    expand.py can solve an in-memory matrix without a round trip to disk.
    """
    n = len(full)
    if symmetric:
        full = antisymmetrize(full)
    holes = [sum(1 for j in range(n) if full[i][j] is None) for i in range(n)]
    keep = [i for i in range(n) if holes[i] <= max_holes]
    dropped = [(i, holes[i]) for i in range(n) if holes[i] > max_holes]
    filled = 0
    m = []
    for i in keep:
        row = []
        for j in keep:
            v = full[i][j]
            if v is None:
                filled += 1
                v = 0.5
            row.append(v)
        m.append(row)
    return m, keep, {"n": n, "dropped": dropped, "filled": filled,
                     "symmetric": symmetric}


def load_matrix(path, max_holes, symmetric=True):
    """Read an arena state file into (matrix, kept indices, report)."""
    with open(path) as f:
        state = json.load(f)
    # arena state files carry the setup count in meta; expand state files
    # carry the armies themselves and grow, so read whichever is there
    n = state["meta"].get("n") or len(state.get("armies", []))
    if not n:
        # arena result files carry no "armies" key, so the old
        # fallback raised KeyError instead of reporting an empty
        # matrix. Derive the size from the cells themselves.
        n = 1 + max((max(int(x) for x in k.split(",")[:2])
                     for k in state.get("cells", {})), default=-1)
    acc = {}
    for key, v in state["cells"].items():
        if v is None:
            continue
        i, j, _g = (int(x) for x in key.split(","))
        acc.setdefault((i, j), []).append(v)
    full = [[(sum(acc[(i, j)]) / len(acc[(i, j)]) if (i, j) in acc else None)
             for j in range(n)] for i in range(n)]
    m, keep, rep = prepare(full, max_holes, symmetric)
    rep["meta"] = state["meta"]
    return m, keep, rep


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--matrix", required=True, help="arena state JSON")
    ap.add_argument("--out", help="write the solved mix here (no default)")
    ap.add_argument("--max-holes", type=int, default=2,
                    help="drop a setup with more unmeasured cells than this")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--raw", action="store_true",
                    help="skip antisymmetrisation (see antisymmetrize())")
    args = ap.parse_args()

    m, keep, rep = load_matrix(args.matrix, args.max_holes,
                               symmetric=not args.raw)
    print("%d setups in the file, %d kept, %d dropped for holes"
          % (rep["n"], len(keep), len(rep["dropped"])))
    for i, h in rep["dropped"]:
        print("   dropped setup %d (%d unmeasured cells)" % (i, h))
    if rep["filled"]:
        print("%d surviving cells were unmeasured and filled with 0.5"
              % rep["filled"])
    if not m:
        raise SystemExit("nothing left to solve; raise --max-holes")

    x, value = nash(m)
    expl = exploitability(m, x)
    print("\nequilibrium value %.4f (0.50 expected for a symmetric game), "
          "exploitability %.4f" % (value, expl))
    print("\nsupport:")
    order = sorted(range(len(x)), key=lambda i: -x[i])
    for i in order[:args.top]:
        if x[i] < 1e-6:
            break
        print("   %6.2f%%  setup %d" % (100 * x[i], keep[i]))

    if args.out:
        with open(args.out, "w") as f:
            json.dump({"pool_index": keep, "weights": x.tolist(),
                       "value": value, "exploitability": expl,
                       "dropped": rep["dropped"], "filled": rep["filled"],
                       "matrix_meta": rep["meta"]}, f, indent=1)
        print("\nwrote %s" % args.out)


if __name__ == "__main__":
    main()
