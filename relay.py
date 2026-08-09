"""Next placement, given the game so far. For driving a real game by hand.

play.py --live wants an interactive stdin session, which is awkward to drive
from a browser. This is the same drafter, called statelessly: hand it every
placement made so far as `@Qd1` tokens and it replays them and prints what to
place next.

    python3 relay.py --color white
    python3 relay.py --color white @Ba1 @Ka8 @Bc1

Mixing is on by default (play.MIX), so the army is drawn from the equilibrium
support -- pass the same --seed every turn or it will draw a different army
mid-game and abandon the pieces it has already placed.
"""

import argparse
import random
import sys

import chess

import play
import rules


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--color", choices=("white", "black"), required=True)
    ap.add_argument("--target", default=play.DEFAULT_TARGET)
    ap.add_argument("--pool", default=play.DEFAULT_POOL)
    ap.add_argument("--seed", type=int, default=2026,
                    help="MUST be the same every turn of one game")
    ap.add_argument("--no-mix", dest="mix", action="store_false", default=play.MIX)
    ap.add_argument("moves", nargs="*", help="every placement so far, in order")
    args = ap.parse_args()

    us = chess.WHITE if args.color == "white" else chess.BLACK
    armies, matrix = play.load_pool(args.pool)
    target = play.load_army(args.target)
    if args.mix:
        drawn = play.sample_target(args.target, armies, random.Random(args.seed))
        if drawn is not None:
            target = drawn

    state = rules.SetupState()
    drafter = play.Drafter(target, us, pool=armies, matrix=matrix)
    for tok in args.moves:
        tok = tok.lstrip("@")
        pt = chess.PIECE_SYMBOLS.index(tok[0].lower())
        sq = chess.parse_square(tok[1:3].lower())
        try:
            state.place(pt, sq)
        except ValueError as e:
            sys.exit("replay failed at %s: %s" % (tok, e))
        # keep the drafter's plan in step with what it has already placed
        if state.board.color_at(sq) == us:
            for k, (tpt, tsq) in enumerate(drafter.target):
                if (tpt, tsq) == (pt, sq):
                    drafter.placed.add(k)

    if state.result:
        print("GAME OVER in the setup phase: %s" % state.result)
        return
    if state.complete:
        print("SETUP COMPLETE")
        print("fen         %s" % state.handoff_fen())
        print("side to move %s" % chess.COLOR_NAMES[state.turn])
        return
    if state.turn != us:
        print("not our turn -- waiting for their placement")
        print("their legal replies: %d" % len(state.legal_placements()))
        return

    pt, sq = drafter.choose(state)
    print("PLACE @%s%s" % (chess.piece_symbol(pt).upper(), chess.square_name(sq)))
    print("  our points left after this: %d"
          % (state.points[us] - rules.PIECE_COST[pt]))
    print("  their points left:          %d" % state.points[not us])
    reach = sum(1 for a in armies
                if drafter._revealed(state, us) <= set(a))
    print("  pool armies still reachable: %d of %d" % (reach, len(armies)))


if __name__ == "__main__":
    main()
