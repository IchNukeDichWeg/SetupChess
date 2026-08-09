"""Does our army survive an opponent who learns it? Simulated on the matrix.

Every other harness here assumes a FIXED opponent field: arena.py fills a
matrix, expand.py breeds against it, match.py A/Bs a toggle against it. None of
them can answer the question that actually matters online, where placements are
visible and the same people play you repeatedly:

    if the opponent watches what you play and counters it, what happens?

This simulates that with no engine at all, by replaying the measured payoff
matrix. The opponent does fictitious play: each round it best-responds to the
empirical distribution of what we have played so far. That is the standard
model of a learning adversary and it is what a human does informally after
losing to the same setup twice.

    python3 adapt.py --state campaigns/expand_v3.json --champion campaigns/champion_v3.json

WHAT THIS CANNOT TELL YOU. The opponent's best response is an argmax over
matrix cells holding 8 pairs each, and the max of many noisy samples is biased
in its own favour -- measured, that bias turned a -30 Elo counter into an
apparent -112. So the LEVELS here are pessimistic by an unknown amount. The
SHAPE is not: that a pure strategy decays as it is learned while a mixture does
not is a property of equilibria, not of this data.
"""

import argparse
import json
import random

import arena
import pool as poolmod
import solve
import stats


def simulate(m, policy, rounds, rng, weights=None):
    """Repeated play against fictitious play. Returns our score each round.

    `policy` is "pure" (always the highest-weight army) or "mix" (drawn from
    `weights` each round). The opponent starts uninformed and from round 1 on
    plays the best response to everything it has seen us play.
    """
    n = len(m)
    pure = max(range(n), key=lambda i: weights[i])
    seen = [0.0] * n
    out = []
    for r in range(rounds):
        if policy == "pure":
            ours = pure
        else:
            ours = rng.choices(range(n), weights=weights, k=1)[0]
        if r == 0:
            theirs = rng.randrange(n)          # uninformed first round
        else:
            total = sum(seen)
            theirs = min(range(n),
                         key=lambda j: sum(seen[i] / total * m[i][j]
                                           for i in range(n)))
        out.append(m[ours][theirs])
        seen[ours] += 1.0
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", required=True, help="campaign JSON with the matrix")
    ap.add_argument("--champion", required=True,
                    help="champion JSON carrying the equilibrium support")
    ap.add_argument("--rounds", type=int, default=200)
    ap.add_argument("--trials", type=int, default=200,
                    help="independent repetitions, averaged")
    ap.add_argument("--max-holes", type=int, default=8)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    with open(args.state) as f:
        state = json.load(f)
    cells = {tuple(int(x) for x in k.split(",")): v
             for k, v in state["cells"].items()}
    m, keep, rep = solve.prepare(
        arena.matrix_from_cells(cells, len(state["armies"])), args.max_holes)
    x, value = solve.nash(m)

    print("pool %d setups, equilibrium value %.4f, exploitability %.4f"
          % (len(m), value, solve.exploitability(m, x)))
    print("%d rounds x %d trials, opponent plays fictitious play\n"
          % (args.rounds, args.trials))

    print("%-6s %-14s %-14s %-14s" % ("round", "pure", "mix", "difference"))
    curves = {}
    for policy in ("pure", "mix"):
        rng = random.Random(args.seed)
        runs = [simulate(m, policy, args.rounds, rng, list(x))
                for _ in range(args.trials)]
        curves[policy] = [sum(r[i] for r in runs) / len(runs)
                          for i in range(args.rounds)]
    marks = [0, 1, 2, 4, 9, 19, 49, 99, args.rounds - 1]
    for r in [i for i in marks if i < args.rounds]:
        p, x_ = curves["pure"][r], curves["mix"][r]
        print("%-6d %-14.4f %-14.4f %+.4f" % (r + 1, p, x_, x_ - p))

    tail = max(1, args.rounds // 2)
    pt = sum(curves["pure"][-tail:]) / tail
    mt = sum(curves["mix"][-tail:]) / tail
    print("\nsecond-half average")
    print("  pure  %.4f  (%+.1f Elo)" % (pt, stats.elo(pt)))
    print("  mix   %.4f  (%+.1f Elo)" % (mt, stats.elo(mt)))
    print("  being predictable costs %.1f Elo once they have learned you"
          % (stats.elo(mt) - stats.elo(pt)))


if __name__ == "__main__":
    main()
