"""Candidate setups: hand-written archetypes plus mutation operators.

An army here is a COMPLETE setup (the end state of the placement game), always
in White's perspective: [(piece_type, square), ...] with exactly one king and
cost <= 39. The placement order that reaches it is play.py's problem; the pool
and the arena only ever see finished armies.

The archetypes are seeds, not opinions. They exist to give the mutation loop
somewhere to start and the arena a baseline to measure the solved strategy
against; none of them is claimed to be good until the matrix says so.
"""

import hashlib
import random

import chess

import rules

# a1=0..h8=63; squares are named so the archetypes read like a board
S = chess.parse_square


def _army(king, spec):
    """spec: {piece_type: "a1 b2 c3"} -> army list, king first."""
    army = [(chess.KING, S(king))]
    for pt, squares in spec.items():
        army.extend((pt, S(n)) for n in squares.split())
    return army


P, N, B, R, Q = (chess.PAWN, chess.KNIGHT, chess.BISHOP,
                 chess.ROOK, chess.QUEEN)

ARCHETYPES = {
    # the ordinary chess army, which is exactly 39 points by construction
    "classic": _army("e1", {P: "a2 b2 c2 d2 e2 f2 g2 h2", N: "b1 g1",
                            B: "c1 f1", R: "a1 h1", Q: "d1"}),
    # same material, king tucked and rooks already connected
    "classic_castled": _army("g1", {P: "a2 b2 c2 d2 e2 f2 g2 h2", N: "b1 f1",
                                    B: "c1 e1", R: "a1 d1", Q: "h1"}),
    # 16 pawns is the maximum wall; the rest buys one heavy battery
    "pawn_wall": _army("e1", {P: ("a2 b2 c2 d2 e2 f2 g2 h2 "
                                  "a3 b3 c3 d3 e3 f3 g3 h3"),
                              Q: "d1", R: "a1 h1"}),
    # the population's favourite: maximum queens, minimum everything else
    "queen_spam": _army("e1", {Q: "a1 b1 c1 d1", N: "g1"}),
    # bishops are the cheapest long-range piece at 3, so buy twelve of them
    "bishop_swarm": _army("e1", {B: "a1 b1 c1 d1 f1 g1 h1 a3 b3 c3 d3 e3",
                                 P: "e2 f2 g2"}),
    # rooks on every open file behind a thin screen
    "rook_battery": _army("e1", {R: "a1 b1 c1 d1 f1 g1", P: "a2 b2 c2 d2"}),
    # heavy pieces, thin pawn cover
    "heavy": _army("g1", {Q: "d1 e1", R: "a1 h1", B: "c1", P: "f2 g2 h2"}),
    # knights want the third rank, where they already hit the fourth
    "knight_horde": _army("e1", {N: "a3 b3 c3 d3 f3 g3 h3", Q: "d1",
                                 P: "e2 f2 g2 h2 a2"}),
    # king buried behind a corner box, everything else long-range
    "fortress": _army("a1", {P: "a2 b2 b3 c3", R: "b1 c1", Q: "d1",
                             B: "e1 f1", N: "g1"}),
    # everything as far forward as the rules allow
    "rank3_rush": _army("e1", {Q: "d3 e3", R: "c3 f3", B: "b3 g3",
                               P: "a3 h3 d2"}),
    # two queens plus a full pawn shield in front of them
    "double_queen": _army("e1", {Q: "c1 f1", P: "a2 b2 c2 d2 e2 f2 g2 h2",
                                 N: "b1", B: "g1", R: "h1"}),
    # minor pieces only, spread wide
    "minors": _army("e1", {N: "b1 c1 f1 g1 b3 g3", B: "a1 d1 h1 c3 f3",
                           P: "e2 d2"}),
}


def _free_squares(army, piece_type, color=chess.WHITE):
    used = {sq for _, sq in army}
    return [sq for sq in rules.placement_squares(piece_type, color)
            if sq not in used]


def complete(army, rng, budget=rules.BUDGET):
    """Spend leftover points on random legal squares, cheapest-first fallback.

    Leftovers are forfeited on the server (docs/RULES.md), so an army that
    cannot spend its last points is legal, just wasteful. This tops one up
    where it can and gives up quietly where it cannot.
    """
    army = list(army)
    while True:
        left = budget - rules.army_cost(army)
        affordable = [pt for pt in rules.BUYABLE
                      if rules.PIECE_COST[pt] <= left]
        options = [(pt, free) for pt in affordable
                   if (free := _free_squares(army, pt))]
        if not options:
            return army
        pt, free = rng.choice(options)
        army.append((pt, rng.choice(free)))


def _mutate_move(army, rng):
    """Relocate one non-king piece."""
    idx = [i for i, (pt, _) in enumerate(army) if pt != chess.KING]
    if not idx:
        return army
    i = rng.choice(idx)
    pt, _ = army[i]
    free = _free_squares(army, pt)
    if not free:
        return army
    out = list(army)
    out[i] = (pt, rng.choice(free))
    return out


def _mutate_swap(army, rng):
    """Replace one piece with a different type, then re-spend the change."""
    idx = [i for i, (pt, _) in enumerate(army) if pt != chess.KING]
    if not idx:
        return army
    i = rng.choice(idx)
    old_pt, sq = army[i]
    out = [x for j, x in enumerate(army) if j != i]
    left = rules.BUDGET - rules.army_cost(out)
    choices = [pt for pt in rules.BUYABLE
               if pt != old_pt and rules.PIECE_COST[pt] <= left
               and sq in set(rules.placement_squares(pt, chess.WHITE))]
    if choices:
        out.append((rng.choice(choices), sq))
    return out


def _mutate_drop(army, rng):
    """Delete one or two pieces and re-spend the points elsewhere."""
    idx = [i for i, (pt, _) in enumerate(army) if pt != chess.KING]
    if not idx:
        return army
    drop = set(rng.sample(idx, min(len(idx), rng.randint(1, 2))))
    return [x for j, x in enumerate(army) if j not in drop]


def _mutate_king(army, rng):
    """Move the king; it is free, so this never touches the budget."""
    out = [x for x in army if x[0] != chess.KING]
    free = _free_squares(out, chess.KING)
    if not free:
        return army
    return [(chess.KING, rng.choice(free))] + out


MUTATORS = (_mutate_move, _mutate_swap, _mutate_drop, _mutate_king)


def crossover(a, b, rng):
    """Take one rank band from a, the rest from b, then re-spend the change.

    Single-piece edits cannot escape a local optimum: measured on the first
    expansion run, 13 of 13 mutants of a 12-bishop champion lost their screen,
    which for equal-strength challengers would happen 0.7% of the time. They
    were genuinely worse, so the champion sits in a hole that one edit cannot
    climb out of. Recombining whole bands moves several pieces at once.
    """
    band = rng.choice(((0,), (1,), (2,), (0, 1), (1, 2)))
    out = [(pt, sq) for pt, sq in a if chess.square_rank(sq) in band]
    used = {sq for _, sq in out}
    for pt, sq in b:
        if sq not in used and rules.army_cost(out) + rules.PIECE_COST[pt] <= rules.BUDGET:
            out.append((pt, sq))
            used.add(sq)
    kings = [x for x in out if x[0] == chess.KING]
    out = [x for x in out if x[0] != chess.KING]
    if kings:
        out.insert(0, kings[0])
    else:
        free = _free_squares(out, chess.KING)
        if not free:
            return list(a)
        out.insert(0, (chess.KING, rng.choice(free)))
    return complete(out, rng)


def mutate(army, rng, steps=1):
    """`steps` random mutations, re-completed to spend what they freed up."""
    out = army
    for _ in range(max(1, steps)):
        out = complete(rng.choice(MUTATORS)(out, rng), rng)
    ok, why = rules.validate_army(out)
    if not ok:  # a mutator may never produce an illegal army
        raise AssertionError("mutation produced %s: %r" % (why, out))
    return out


def breed(parents, rng, crossover_rate=0.35, max_steps=3):
    """One challenger from a parent list: crossover, else a multi-step mutation."""
    if len(parents) >= 2 and rng.random() < crossover_rate:
        a, b = rng.sample(parents, 2)
        out = crossover(a, b, rng)
        # a band that covers all of one parent, or none of it, recombines to
        # that parent exactly (measured: 36% of classic x queen_spam children).
        # That is arithmetically right but useless as a challenger, so nudge it.
        if army_key(out) in (army_key(a), army_key(b)):
            out = mutate(out, rng)
        ok, why = rules.validate_army(out)
        if not ok:
            raise AssertionError("crossover produced %s: %r" % (why, out))
        return out
    # most edits are single-step; the tail is what escapes a local optimum
    steps = rng.choices(range(1, max_steps + 1),
                        weights=[2 ** -k for k in range(max_steps)])[0]
    return mutate(rng.choice(parents), rng, steps)


def army_key(army):
    """Order-independent identity, so the pool never holds a duplicate."""
    return tuple(sorted(army))


def fingerprint(army):
    """Short printable identity for release notes and frozen snapshots.

    Goes through army_key, so it does NOT depend on the order the placements
    happen to be stored in. The v1 notes carry `ec385f649cf8af3e`, which was
    sha256 of the army in its stored order and therefore gave two different
    values for the same army depending on serialisation -- the same trap that
    made two identical matchup pools look different mid-campaign. That value is
    kept in docs/RELEASES.md as published; anything from v2 on uses this.
    """
    return hashlib.sha256(repr(army_key(tuple(tuple(p) for p in army)))
                          .encode()).hexdigest()[:16]


# What chess.com's bot ACTUALLY drafts, transcribed from two live games on the
# analysis board. Both as Black, stored here in White's perspective like every
# other army. Both spend the full 39 points and both buy THREE QUEENS.
#
#   2026-08-05  K, 3 queens, 1 rook, 1 bishop, 4 pawns
#   2026-08-08  K, 3 queens, 2 knights, 1 bishop, 3 pawns
#
# This REFUTES the piece-preference half of docs/BOT_MODEL.md, which predicted
# a drift toward pawns and knights from a small standing bias against expensive
# pieces (queen -0.3, rook -0.4) and material being absent from the setup eval.
# The king clause is confirmed exactly -- a corner on the first placement, both
# games -- but the bot buys the dearest piece on the board, repeatedly.
BOT_OBSERVED = {
    "2026-08-05": [(chess.PAWN, chess.G2),
     (chess.PAWN, chess.H2),
     (chess.PAWN, chess.A3),
     (chess.PAWN, chess.B3),
     (chess.BISHOP, chess.C3),
     (chess.ROOK, chess.B1),
     (chess.QUEEN, chess.C1),
     (chess.QUEEN, chess.A2),
     (chess.QUEEN, chess.B2),
     (chess.KING, chess.A1)],
    "2026-08-08": [(chess.PAWN, chess.H2),
     (chess.PAWN, chess.A3),
     (chess.PAWN, chess.E3),
     (chess.KNIGHT, chess.B3),
     (chess.KNIGHT, chess.C3),
     (chess.BISHOP, chess.G3),
     (chess.QUEEN, chess.B1),
     (chess.QUEEN, chess.A2),
     (chess.QUEEN, chess.B2),
     (chess.KING, chess.A1)],
}


# What HUMANS actually draft, transcribed from live rated games. Same storage
# convention: both were played as Black, stored here in White's perspective.
#
#   2026-08-08  13 bishops, no pawns (also in campaigns/pool_13bishop.json)
#   2026-08-10  15 pawns, 2 knights, bishop, rook, queen -- a full wall
#
# The wall matters more than its result. `BOT_WALL` below is the pawn-and-
# knight army docs/BOT_MODEL.md predicted the BOT would build, and two live
# games refuted it: the bot buys queens. Nobody had seen the wall played at
# all, so it was written off as a modelling error. A 1826-rated human then
# drafted almost exactly it. The model was wrong about WHO plays a wall, not
# about whether anyone does, and the real-opponent field was one archetype
# short until this game.
#
# 38 of 39 points, so a point went unspent -- transcribed as played, not
# corrected.
HUMAN_OBSERVED = {
    "2026-08-10-wall": [(chess.KING, chess.D1),
     (chess.QUEEN, chess.C1),
     (chess.ROOK, chess.A1),
     (chess.BISHOP, chess.B2),
     (chess.KNIGHT, chess.E1),
     (chess.KNIGHT, chess.G1),
     (chess.PAWN, chess.A2),
     (chess.PAWN, chess.C2),
     (chess.PAWN, chess.D2),
     (chess.PAWN, chess.E2),
     (chess.PAWN, chess.F2),
     (chess.PAWN, chess.G2),
     (chess.PAWN, chess.H2),
     (chess.PAWN, chess.A3),
     (chess.PAWN, chess.B3),
     (chess.PAWN, chess.C3),
     (chess.PAWN, chess.D3),
     (chess.PAWN, chess.E3),
     (chess.PAWN, chess.F3),
     (chess.PAWN, chess.G3),
     (chess.PAWN, chess.H3)],
}

# SUPERSEDED, kept because --seed-bot campaigns were run against it and their
# results are meaningless without knowing what they faced. This is what
# play.BotDrafter drafts from the modelled policy: king to a corner, then every
# pawn square filled and the rest of rank 1 in knights, 37 of 39 points.
#
# It is not what the bot plays. Do not seed campaigns with it.
BOT_WALL = [(chess.PAWN, sq) for sq in
            (chess.A2, chess.B2, chess.C2, chess.D2, chess.E2, chess.F2,
             chess.G2, chess.H2, chess.A3, chess.B3, chess.C3, chess.D3,
             chess.E3, chess.F3, chess.G3, chess.H3)] + \
           [(chess.KNIGHT, sq) for sq in
            (chess.B1, chess.C1, chess.D1, chess.E1, chess.F1, chess.G1,
             chess.H1)] + [(chess.KING, chess.A1)]


def seed_pool(rng, size=None):
    """Archetypes first, then mutations of them until size is reached."""
    pool, seen = [], set()
    for army in ARCHETYPES.values():
        army = complete(army, rng)
        ok, why = rules.validate_army(army)
        if not ok:
            raise AssertionError("archetype invalid: %s" % why)
        if army_key(army) not in seen:
            seen.add(army_key(army))
            pool.append(army)
    while size is not None and len(pool) < size:
        cand = mutate(rng.choice(pool), rng)
        if army_key(cand) not in seen:
            seen.add(army_key(cand))
            pool.append(cand)
    return pool


def to_json(army):
    return [[pt, sq] for pt, sq in army]


def from_json(data):
    return [(pt, sq) for pt, sq in data]


if __name__ == "__main__":
    rng = random.Random(0)
    for name, army in ARCHETYPES.items():
        filled = complete(army, rng)
        print("%-16s %2d points -> %2d after top-up, %2d pieces" % (
            name, rules.army_cost(army), rules.army_cost(filled), len(filled)))
