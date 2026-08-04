"""Play a full Setup Chess game: draft the army, hand off, relay moves.

The drafting half is the whole point; once both armies are down the position
is ordinary chess and a UCI engine plays it.

One rule shapes the entire placement policy: each side places only inside its
own three ranks, so an opponent can never take a square we want. Placement is
alternating and visible (docs/RULES.md), but the only real interaction during
setup is CHECK -- a placement that attacks the enemy king forces a block, and
a check with no legal block ends the game there. So the policy is:

  1. if a placement mates them during setup, play it and win before move one
  2. if we are in check, we are forced -- pick the block that best serves the
     target army
  3. otherwise place the next piece of the target army
  4. hold the king back, then place it on the safest square that is still
     legal, before the budget runs out

Opponent placements arrive either from a local policy (offline gate) or from
stdin, one `@Qd1` token per line, for driving a real game elsewhere.
"""

import argparse
import json
import sys

import chess
import chess.engine

import arena
import pool as poolmod
import rules
import solve

# Placing the king first invites a rank-3 checkmate before move one (the live
# @Qe1+# game in docs/RULES.md). Holding it to the very end is also wrong: the
# square may be attacked by then and the fallbacks are worse. This spends most
# of the budget first, then places it while there is still room to manoeuvre.
KING_AT_POINTS_LEFT = 12

# Hunt an unplaced enemy king once its safe squares are this few. 6 measured
# strictly better than 8: both win the same three of four king-last styles,
# but at 8 the drafter abandoned 5 of its 14 target pieces chasing a dense
# opponent that was never in danger, and at 6 it builds all 14. Ending setup
# without a king is an outright loss, not a checkmate ("failed to set up his
# king" in the shipped client), so covering every empty square in their zone
# wins on its own. Measured against a 23/24-coverage army: sparse armies that
# hold the king back get locked out, dense ones survive because their own
# sixteen pieces block every ray to the back rank.
HUNT_WHEN = 6

# ...but only once they can no longer buy their way out. Hunting a king that
# is going to be placed anyway just wrecks our own army: with the threshold
# alone the drafter built 9 of its 14 target pieces against a dense opponent
# that was never in danger. A side still holding a big budget can fill its
# zone with blockers and hand the squares back, so the hunt waits until their
# remaining points are this low.
HUNT_THEIR_POINTS = 12


def load_pool(path):
    """Armies and the antisymmetrised payoff matrix from a campaign state."""
    with open(path) as f:
        state = json.load(f)
    armies = [poolmod.from_json(a) for a in state["armies"]]
    cells = {tuple(int(x) for x in k.split(",")): v
             for k, v in state["cells"].items()}
    full = solve.antisymmetrize(arena.matrix_from_cells(cells, len(armies)))
    return armies, full


class Drafter:
    """Realises a target army, with tactics taking priority over the plan.

    Priority each turn:
      1. a placement that mates them during setup
      2. re-target: best response to what their revealed pieces imply,
         restricted to pool armies still reachable from what we have placed
      3. hunt an unplaced enemy king once its safe squares are running out
      4. place our own king before the budget runs down
      5. follow the plan (a check restricts the legal set, so this becomes
         "block" on its own)

    Re-targeting is only possible while we are not yet committed: an army is
    reachable exactly when every piece we have already placed appears in it,
    so the option set narrows with each placement. That is why the opening
    placements matter more than the closing ones.

    Re-targeting is OPT-IN (pass a pool) and measured negative on the only
    screen that exists so far: it drops from 3/4 to 2/4 setup-phase wins
    against the four opponent styles below, giving up a forced mate against
    queen spam. The cause is that the payoff matrix is measured by playing
    the CHESS phase from two finished armies -- arena.py never simulates the
    placement game -- so the matrix cannot see a setup mate or a lockout and
    happily trades one away for a slightly better middlegame. That screen
    cannot see re-targeting's upside either, since it only scores the setup
    phase. Judging it needs a full-game match; until then the default is off.
    """

    def __init__(self, target, color, pool=None, matrix=None,
                 hunt_when=HUNT_WHEN):
        self.color = color
        self.target = (target if color == chess.WHITE
                       else rules.mirror_army(target))
        self.placed = set()
        self.pool = pool
        self.matrix = matrix
        self.hunt_when = hunt_when
        self.retargets = 0

    # --- best response ---------------------------------------------------

    def _own_perspective(self, squares_pieces, color):
        """Pieces of `color` as a White-perspective set, to match the pool."""
        if color == chess.WHITE:
            return {(pt, sq) for pt, sq in squares_pieces}
        return {(pt, chess.square_mirror(sq)) for pt, sq in squares_pieces}

    def _revealed(self, state, color):
        return self._own_perspective(
            [(p.piece_type, sq) for sq, p in state.board.piece_map().items()
             if p.color == color], color)

    def _opponent_weights(self, state):
        """How much each pool army looks like what they have shown so far."""
        opp = not self.color
        shown = self._revealed(state, opp)
        if not shown:
            return [1.0 / len(self.pool)] * len(self.pool)
        raw = []
        for army in self.pool:
            hits = len(shown & set(army))
            raw.append((hits / len(shown)) ** 4)  # sharpen; ties stay ties
        total = sum(raw)
        if total <= 0:
            return [1.0 / len(self.pool)] * len(self.pool)
        return [r / total for r in raw]

    def _retarget(self, state):
        """Swap the plan for the best still-reachable answer to their army."""
        if not self.pool or self.matrix is None:
            return
        ours = self._revealed(state, self.color)
        reachable = [i for i, a in enumerate(self.pool)
                     if ours <= set(a)]
        if len(reachable) < 2:
            return
        w = self._opponent_weights(state)
        best, best_val = None, None
        for i in reachable:
            row = self.matrix[i]
            val = sum(w[j] * (row[j] if row[j] is not None else 0.5)
                      for j in range(len(self.pool)))
            if best_val is None or val > best_val:
                best, best_val = i, val
        if best is None:
            return
        want = (self.pool[best] if self.color == chess.WHITE
                else rules.mirror_army(self.pool[best]))
        if set(want) != set(self.target):
            self.target = want
            self.placed = set()
            self.retargets += 1

    # --- king hunt -------------------------------------------------------

    def _their_safe_squares(self, state):
        """Empty squares in their zone their king could still legally take.

        None once their king is on the board -- the hunt is over then.
        """
        opp = not self.color
        if state.board.king(opp) is not None:
            return None
        out = []
        for r in rules.zone_ranks(opp):
            for f in range(8):
                sq = chess.square(f, r)
                if state.board.piece_at(sq):
                    continue
                if state.board.is_attacked_by(self.color, sq):
                    continue
                out.append(sq)
        return out

    def _hunt_move(self, state, legal, safe):
        """The placement that removes the most of their remaining king squares.

        ponytail: greedy, one ply. Their own later placements can block our
        rays and hand squares back, so a count of zero is pressure rather than
        a proven win. Upgrade path: search the placement tree for a forced
        lockout, which is what the C core is for.
        """
        target_sq = {sq for _, sq in self.target}
        best, best_key = None, None
        for pt, sq in legal:
            if pt == chess.KING:
                continue
            state.board.set_piece_at(sq, chess.Piece(pt, self.color))
            left = sum(1 for s in safe
                       if not state.board.is_attacked_by(self.color, s))
            state.board.remove_piece_at(sq)
            # fewest squares left, then stay on plan, then spend cheaply
            key = (left, 0 if sq in target_sq else 1, rules.PIECE_COST[pt])
            if best_key is None or key < best_key:
                best, best_key = (pt, sq), key
        return best

    def _remaining(self, state):
        """Target pieces not yet on the board, dearest first."""
        out = []
        for k, (pt, sq) in enumerate(self.target):
            if k in self.placed or state.board.piece_at(sq):
                continue
            out.append((k, pt, sq))
        out.sort(key=lambda t: -rules.PIECE_COST[t[1]])
        return out

    def _mate_in_one(self, state, legal):
        """A placement the opponent cannot answer ends the game immediately."""
        opp = not self.color
        for pt, sq in legal:
            state.board.set_piece_at(sq, chess.Piece(pt, self.color))
            mates = (state.in_check(opp) and not state._placements(opp)
                     and not (opp == chess.WHITE and state.points[opp] == 0))
            state.board.remove_piece_at(sq)
            if mates:
                return (pt, sq)
        return None

    def _king_square(self, state, legal):
        """Safest legal king square: prefer the target's, else fewest attackers."""
        opts = [(pt, sq) for pt, sq in legal if pt == chess.KING]
        if not opts:
            return None
        want = [sq for pt, sq in self.target if pt == chess.KING]
        for pt, sq in opts:
            if want and sq == want[0]:
                return (pt, sq)
        opp = not self.color

        def exposure(sq):
            # count enemy pieces bearing on the square's neighbourhood
            n = 0
            for around in chess.SquareSet(chess.BB_KING_ATTACKS[sq] | (1 << sq)):
                n += len(state.board.attackers(opp, around))
            return n

        return min(opts, key=lambda t: exposure(t[1]))

    def choose(self, state):
        legal = state.legal_placements()
        if not legal:
            raise ValueError("no legal placement for %s"
                             % chess.COLOR_NAMES[self.color])
        legal_set = set(legal)

        mate = self._mate_in_one(state, legal)
        if mate:
            return mate

        self._retarget(state)

        safe = self._their_safe_squares(state)
        opp_points = state.points[not self.color]
        if (safe is not None and len(safe) <= self.hunt_when
                and opp_points <= HUNT_THEIR_POINTS):
            hunt = self._hunt_move(state, legal, safe)
            if hunt:
                return hunt

        must_king = state.board.king(self.color) is None
        if must_king and state.points[self.color] <= KING_AT_POINTS_LEFT:
            king = self._king_square(state, legal)
            if king:
                return king

        # follow the plan where the plan is still legal (a check restricts
        # `legal` to blocking squares, so this silently becomes "block")
        for k, pt, sq in self._remaining(state):
            if (pt, sq) in legal_set:
                self.placed.add(k)
                return (pt, sq)

        # off-plan: spend the most expensive affordable piece, cheapest square
        if must_king:
            king = self._king_square(state, legal)
            if king:
                return king
        return max(legal, key=lambda t: rules.PIECE_COST[t[0]])


class StdinDrafter:
    """Reads the opponent's placements as `@Qd1` tokens, one per line."""

    def __init__(self, color):
        self.color = color

    def choose(self, state):
        line = sys.stdin.readline()
        if not line:
            raise SystemExit("opponent stream closed mid-setup")
        tok = line.strip().lstrip("@")
        if not tok:
            return self.choose(state)
        pt = chess.PIECE_SYMBOLS.index(tok[0].lower())
        return pt, chess.parse_square(tok[1:3].lower())


def draft(white, black, log=None):
    """Run the placement phase. Returns the finished SetupState."""
    state = rules.SetupState()
    drafters = {chess.WHITE: white, chess.BLACK: black}
    for _ in range(200):
        if state.result or state.complete:
            break
        mover = state.turn
        pt, sq = drafters[mover].choose(state)
        state.place(pt, sq)
        if log is not None:
            log.append("@%s%s" % (chess.piece_symbol(pt).upper(),
                                  chess.square_name(sq)))
    else:
        raise RuntimeError("placement did not terminate")
    return state


def play_out(state, engine, nodes, log=None):
    """Play the handed-off position to a result. Returns a result string."""
    fen = state.handoff_fen()
    ok, why = rules.validate_fen(fen)
    if not ok:
        raise ValueError("refusing to hand over an invalid position: %s" % why)
    ok, why = rules.engine_safe(fen)
    if not ok:
        # legal Setup Chess, but this engine would crash on it; say so rather
        # than starting a game that dies halfway
        return "*", fen, why
    board = chess.Board(fen)
    limit = chess.engine.Limit(nodes=nodes)
    while not board.is_game_over(claim_draw=True) and board.ply() < 400:
        move = engine.play(board, limit).move
        if move is None:
            break
        if log is not None:
            log.append(board.san(move))
        board.push(move)
    outcome = board.outcome(claim_draw=True)
    return (outcome.result() if outcome else "1/2-1/2"), fen, ""


def load_army(path_or_name):
    """A champion JSON path, or an archetype name."""
    if path_or_name in poolmod.ARCHETYPES:
        import random
        return poolmod.complete(poolmod.ARCHETYPES[path_or_name],
                                random.Random(0))
    with open(path_or_name) as f:
        data = json.load(f)
    return poolmod.from_json(data["army"] if "army" in data else data)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", required=True,
                    help="our army: champion JSON path or archetype name")
    ap.add_argument("--opponent", default="classic",
                    help="their army: path, archetype name, or 'stdin'")
    ap.add_argument("--engine", default="stockfish")
    ap.add_argument("--nodes", type=int, default=20000)
    ap.add_argument("--color", choices=("white", "black", "both"),
                    default="both", help="'both' plays one game each way")
    ap.add_argument("--pool", help="campaign state JSON; enables best-response "
                    "re-targeting against the opponent's revealed army")
    ap.add_argument("--hunt-when", type=int, default=HUNT_WHEN,
                    help="hunt an unplaced enemy king at this many safe squares")
    ap.add_argument("--out", help="write the game log here (no default)")
    args = ap.parse_args()

    ours = load_army(args.target)
    ok, why = rules.validate_army(ours)
    if not ok:
        sys.exit("our army is illegal: %s" % why)

    armies = matrix = None
    if args.pool:
        armies, matrix = load_pool(args.pool)
        print("best-response enabled over %d pool armies" % len(armies))

    colors = ({"white": [chess.WHITE], "black": [chess.BLACK],
               "both": [chess.WHITE, chess.BLACK]})[args.color]
    engine = chess.engine.SimpleEngine.popen_uci(args.engine)
    games = []
    try:
        for color in colors:
            us = Drafter(ours, color, pool=armies, matrix=matrix,
                         hunt_when=args.hunt_when)
            if args.opponent == "stdin":
                them = StdinDrafter(not color)
            else:
                them = Drafter(load_army(args.opponent), not color)
            drafters = {color: us, not color: them}
            log = []
            state = draft(drafters[chess.WHITE], drafters[chess.BLACK], log)
            if state.result:
                result, fen, note = state.result, "", "setup checkmate"
            else:
                result, fen, note = play_out(state, engine, args.nodes, log)
            side = chess.COLOR_NAMES[color]
            print("we are %-5s -> %s%s" % (side, result,
                                           note and "  (%s)" % note or ""))
            print("   setup: %s" % " ".join(log[:len(log)] if state.result
                                            else log[:_setup_len(state)]))
            if fen:
                print("   fen:   %s" % fen)
            games.append({"our_color": side, "result": result, "fen": fen,
                          "note": note, "log": log, "retargets": us.retargets})
    finally:
        engine.quit()

    if args.out:
        with open(args.out, "w") as f:
            json.dump({"target": args.target, "opponent": args.opponent,
                       "nodes": args.nodes, "games": games}, f, indent=1)
        print("wrote %s" % args.out)


def _setup_len(state):
    """How many log entries were placements rather than moves."""
    return len(state.board.piece_map())


if __name__ == "__main__":
    main()
