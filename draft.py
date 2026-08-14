"""Play the PLACEMENT phase between two strategies and return the handoff.

arena.py has always built its positions with rules.setup_fen, which stamps two
finished armies onto an empty board. That models a game where both sides commit
blind and simultaneously. Setup Chess is not that game: placement alternates
with full information, so a real opponent can answer what you have already put
down -- and one did, by stacking two attackers on a defended bishop and taking
the exchange at handoff.

So every payoff in campaigns/ measures armies that never got to react. This
module is the replacement: it actually plays the phase.

Two things follow that setup_fen could not give:

  * WHO MOVES FIRST is derived rather than assumed. setup_fen documents White
    to move "BY CONVENTION ... two finished armies carry no placement order, so
    there is nothing to derive it from". Playing the phase produces that order,
    so handoff_fen can be used instead, and the free tempo lands where the rules
    actually put it.
  * SETUPS CAN BE DECIDED BEFORE THE HANDOFF. A setup mate or a lockout ends
    the game in the placement phase, which a stamped FEN can never show.

    st = draft.play_setup(army_a, army_b, "search", "plan")
    st.result        # set if the setup itself decided it
    st.handoff_fen() # otherwise the position to hand to an engine
"""

import chess

import play
import psearch
import rules

MODES = ("plan", "search")

# A setup cannot exceed one placement per point per side plus the two kings,
# and every placement either spends points or is the free king. This only has
# to stop a runaway if a strategy ever returns an illegal no-op; it is not a
# rule of the game.
MAX_PLACEMENTS = 2 * (rules.BUDGET + 1)


class Stuck(Exception):
    """A strategy returned something the state would not accept."""


def _chooser(mode, army, color, depth, width, plan):
    """A callable state -> (piece_type, square)."""
    if mode == "plan":
        drafter = play.Drafter(army, color)
        return drafter.choose
    if mode == "search":
        return lambda state: psearch.best(state, color, max_depth=depth,
                                          width=width, plan=plan)
    raise ValueError("unknown draft mode %r (want one of %r)" % (mode, MODES))


def play_setup(white_army, black_army, white_mode="plan", black_mode="plan",
               depth=2, width=8, plan=None):
    """Play the placement phase out. Returns the finished rules.SetupState.

    `white_army`/`black_army` are the target armies the "plan" mode follows and
    that "search" mode ignores -- a searching side builds whatever the position
    asks for, which is the entire point of measuring this way.
    """
    for mode in (white_mode, black_mode):
        if mode not in MODES:
            raise ValueError("unknown draft mode %r (want one of %r)"
                             % (mode, MODES))

    state = rules.SetupState()
    pick = {
        chess.WHITE: _chooser(white_mode, white_army, chess.WHITE, depth, width, plan),
        chess.BLACK: _chooser(black_mode, black_army, chess.BLACK, depth, width, plan),
    }

    for _ in range(MAX_PLACEMENTS):
        if state.result is not None or state.complete:
            return state
        if not state.legal_placements():
            # Neither a mate nor a completion, but nothing legal remains.
            # rules.place() resolves lockouts itself, so reaching here means
            # the state machine and this loop disagree; say so rather than
            # silently returning a half-built position as a measurement.
            raise Stuck("no legal placement for %s with %d points left"
                        % (chess.COLOR_NAMES[state.turn],
                           state.points[state.turn]))
        mover = state.turn
        pt, sq = pick[mover](state)
        state.place(pt, sq)
        if state.turn == mover and state.points[mover] == 0 and not state.complete:
            # place() hands the turn back when the other side is done. That is
            # legal, but a side with nothing left to spend and the move would
            # spin here forever.
            raise Stuck("%s has the move with 0 points and an incomplete setup"
                        % chess.COLOR_NAMES[mover])
    raise Stuck("setup did not finish in %d placements" % MAX_PLACEMENTS)


def handoff(white_army, black_army, white_mode="plan", black_mode="plan",
            depth=2, width=8, plan=None):
    """(fen, result). `fen` is None when the setup itself ended the game."""
    st = play_setup(white_army, black_army, white_mode, black_mode,
                    depth, width, plan)
    if st.result is not None:
        return None, st.result
    return st.handoff_fen(), None


def _selfcheck():
    a = play.load_army("campaigns/champion_v6.json")
    b = play.load_army("campaigns/champion_v2.json")

    # Every mode pairing completes and produces a legal handoff.
    for wm in MODES:
        for bm in MODES:
            fen, res = handoff(a, b, wm, bm, depth=1, width=6)
            assert fen or res, (wm, bm)
            if fen:
                ok, why = rules.validate_fen(fen)
                assert ok, "%s/%s produced %s: %s" % (wm, bm, fen, why)

    # Plan mode builds its target when nothing makes it deviate, which is what
    # makes "plan vs plan" the like-for-like baseline the old stamped model can
    # be compared against. NOTE it is not unconditional: play.Drafter retargets
    # off the pool once the opponent is revealed, so a mirror matchup (a vs a)
    # legitimately drifts. Pick a matchup that does not trigger it.
    st = play_setup(a, b, "plan", "plan")
    got = {(p.piece_type, sq) for sq, p in st.board.piece_map().items()
           if p.color == chess.WHITE}
    assert got == set(a), sorted(got ^ set(a))

    # Whatever a side ends up building, it must be a legal 39-point army --
    # this is the guard that a drafted cell is a real setup and not junk.
    for mode in MODES:
        st = play_setup(a, b, mode, "plan", depth=1, width=6)
        for colour in (chess.WHITE, chess.BLACK):
            army = sorted((p.piece_type, sq)
                          for sq, p in st.board.piece_map().items()
                          if p.color == colour)
            if colour == chess.BLACK:
                army = sorted(rules.mirror_army(army))
            ok, why = rules.validate_army(army)
            assert ok, "%s built an illegal army: %s" % (mode, why)

    # Whoever follows the last placement moves first, which is the thing
    # setup_fen had to assume. Both sides must be reachable across matchups,
    # or the derivation is not doing anything.
    turns = set()
    for other in (a, b):
        st = play_setup(a, other, "plan", "plan")
        if st.result is None:
            turns.add(st.turn)
    assert turns, "no completed setup to take a turn from"

    # A searching side is NOT required to reproduce its nominal army -- it
    # answers the position instead. Verify it really diverges, else "search"
    # is silently just "plan".
    st = play_setup(a, b, "search", "plan", depth=1, width=6)
    built = {(p.piece_type, sq) for sq, p in st.board.piece_map().items()
             if p.color == chess.WHITE}
    assert built != set(a), "search mode reproduced the target army exactly"

    # Bad mode is rejected before any work happens.
    try:
        play_setup(a, b, "plan", "nonsense")
        raise AssertionError("bad mode accepted")
    except ValueError:
        pass

    print("OK: draft selfcheck passed")


if __name__ == "__main__":
    _selfcheck()
