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

import pool as poolmod
import rules

# Placing the king first invites a rank-3 checkmate before move one (the live
# @Qe1+# game in docs/RULES.md). Holding it to the very end is also wrong: the
# square may be attacked by then and the fallbacks are worse. This spends most
# of the budget first, then places it while there is still room to manoeuvre.
KING_AT_POINTS_LEFT = 12


class Drafter:
    """Realises a target army, with tactics taking priority over the plan.

    ponytail: the target is chosen once, up front. Because the opponent
    cannot touch our squares, the only reason to switch mid-draft is their
    revealed army, and re-targeting has to respect what we have already
    spent. Upgrade path: solve.best_response over the pool armies still
    reachable from our current placements, re-run each turn.
    """

    def __init__(self, target, color):
        self.color = color
        self.target = (target if color == chess.WHITE
                       else rules.mirror_army(target))
        self.placed = set()

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
    ap.add_argument("--out", help="write the game log here (no default)")
    args = ap.parse_args()

    ours = load_army(args.target)
    ok, why = rules.validate_army(ours)
    if not ok:
        sys.exit("our army is illegal: %s" % why)

    colors = ({"white": [chess.WHITE], "black": [chess.BLACK],
               "both": [chess.WHITE, chess.BLACK]})[args.color]
    engine = chess.engine.SimpleEngine.popen_uci(args.engine)
    games = []
    try:
        for color in colors:
            us = Drafter(ours, color)
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
                          "note": note, "log": log})
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
