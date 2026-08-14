"""Iterative-deepening search over the PLACEMENT phase.

Everything else in this repo treats an army as a shopping list: pick 39 points
of pieces up front, then fill the squares in. arena.py never plays the
placement phase at all -- it stamps two finished armies onto a board with
rules.setup_fen and hands the position to an engine. So every number measured
here so far compares armies that were built blind and never got to answer each
other.

The real game is not that. Placement alternates with FULL INFORMATION: you see
their pieces as they land and they see yours. That is a game tree, and this
searches it. It was written after a real opponent beat a shipped champion by
stacking two attackers on a single defended bishop and taking the exchange at
handoff -- a plan the plan-following drafter could not see coming and would
walk into again.

Iterative deepening, so a caller can watch the answer improve and stop whenever
it likes:

    for depth, (pt, sq), score in psearch.search(state, chess.WHITE):
        print(depth, chess.square_name(sq), score)

The leaf is a static exchange evaluation, which is the cheapest thing that
sees the mechanism above. Material does not appear in it: both sides spend the
same 39 points, so committed-plus-remaining is a constant and cancels.
"""

import chess

import rules

# Centipawns. KING is a stand-in for "never worth trading" -- a king capture
# cannot actually happen (rules.py ends the setup on mate), so this only has
# to be big enough to dominate every real exchange.
VALUE = {chess.PAWN: 100, chess.KNIGHT: 300, chess.BISHOP: 300,
         chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 10000}

MATE_SCORE = 100000

# Placement branching is enormous: six piece types times up to ~24 squares is
# several hundred moves at the root, where ordinary chess has ~35. A full-width
# search is hopeless, so each ply keeps only the BEAM_WIDTH best-looking
# placements by a 1-ply score.
# ponytail: a fixed beam, not a real selective search -- widen it, or replace
# it with null-move/LMR-style reductions, if the search starts missing plans
# that a human finds.
BEAM_WIDTH = 12
MAX_DEPTH = 6


def see(board, sq, side):
    """Centipawns `side` wins by starting a capture sequence on `sq`.

    Standard swap-off: both sides recapture with their least valuable attacker
    and either may stop when continuing is bad for them. Zero when there is
    nothing to take or `side` owns the piece.

    This is what catches the double-stack. One attacker on a defended bishop is
    a losing trade and scores 0; the SECOND attacker makes it +200 (bishop for
    a knight, say), and the search will now pay to prevent that.

    ponytail: no x-ray handling, so a rook lined up behind a rook is not
    counted. Add it if positions start being misjudged through batteries.
    """
    victim = board.piece_type_at(sq)
    if victim is None or board.color_at(sq) == side:
        return 0

    b = board.copy(stack=False)
    gains = []
    on_square = VALUE[victim]
    turn = side

    while True:
        attackers = b.attackers(turn, sq)
        if not attackers:
            break
        frm = min(attackers, key=lambda s: VALUE[b.piece_type_at(s)])
        gains.append(on_square)
        on_square = VALUE[b.piece_type_at(frm)]
        b.set_piece_at(sq, b.piece_at(frm))
        b.remove_piece_at(frm)
        turn = not turn

    # Fold backwards: at each step the capturing side takes the exchange only
    # if it beats standing pat, which is what makes a defended piece safe.
    score = 0
    for g in reversed(gains):
        score = max(0, g - score)
    return score


def _worst_exchange(board, victim_color):
    """Best single capture sequence available AGAINST `victim_color`."""
    best = 0
    for sq, piece in board.piece_map().items():
        if piece.color != victim_color:
            continue
        v = see(board, sq, not victim_color)
        if v > best:
            best = v
    return best


def leaf(state, us):
    """Static score of a setup position, in centipawns, from `us`.

    Only exchanges appear here. Both sides get the same 39 points, so material
    committed plus material still affordable is a constant for each side and
    cancels out of any difference.

    ponytail: the WORST single exchange per side, not the sum of all of them.
    Only one capture can actually be played first, so summing would double-count
    a position with several loose pieces. It also means the search happily
    leaves two hanging pieces rather than one -- fix by scoring the top two if
    that shows up in real games.
    """
    return _worst_exchange(state.board, not us) - _worst_exchange(state.board, us)


def _terminal(state, us):
    """Score if the setup has already been decided, else None."""
    if state.result is None:
        return None
    if state.result == "1/2-1/2":
        return 0
    winner = chess.WHITE if state.result == "1-0" else chess.BLACK
    return MATE_SCORE if winner == us else -MATE_SCORE


def _make(state, pt, sq):
    """Apply a placement, returning what _unmake needs. place() only ever ADDS
    one piece, so undoing it is removing that piece and restoring the scalars --
    the same in-place idiom play.py._safe_placements uses, and far cheaper than
    cloning the state at every node."""
    snap = (state.turn, state.points[chess.WHITE], state.points[chess.BLACK],
            state.result)
    state.place(pt, sq)
    return snap


def _unmake(state, sq, snap):
    state.board.remove_piece_at(sq)
    (state.turn, state.points[chess.WHITE], state.points[chess.BLACK],
     state.result) = snap


def _ordered(state, us, width):
    """The `width` most promising placements, best first, by a 1-ply score."""
    scored = []
    for pt, sq in state.legal_placements():
        snap = _make(state, pt, sq)
        s = _terminal(state, us)
        if s is None:
            s = leaf(state, us)
        _unmake(state, sq, snap)
        scored.append((s, pt, sq))
    # The side to move wants its own score high; `us` is fixed, so the opponent
    # sorts the other way.
    scored.sort(key=lambda t: t[0], reverse=(state.turn == us))
    return [(pt, sq) for _, pt, sq in scored[:width]]


def _ab(state, us, depth, alpha, beta, width):
    s = _terminal(state, us)
    if s is not None:
        return s
    if depth == 0 or state.complete:
        return leaf(state, us)

    moves = _ordered(state, us, width)
    if not moves:
        return leaf(state, us)

    maximising = state.turn == us
    best = -MATE_SCORE * 2 if maximising else MATE_SCORE * 2
    for pt, sq in moves:
        snap = _make(state, pt, sq)
        try:
            v = _ab(state, us, depth - 1, alpha, beta, width)
        finally:
            _unmake(state, sq, snap)
        if maximising:
            if v > best:
                best = v
            if best > alpha:
                alpha = best
        else:
            if v < best:
                best = v
            if best < beta:
                beta = best
        if alpha >= beta:
            break
    return best


def search(state, us, max_depth=MAX_DEPTH, width=BEAM_WIDTH):
    """Yield (depth, (piece_type, square), score) once per completed depth.

    A generator so the caller owns the budget: break out on a clock, after a
    depth, or when the move stops changing. Nothing here reads a timer, which
    keeps the same code usable for a 3-second live placement and an overnight
    analysis.
    """
    if state.turn != us:
        raise ValueError("not %s to place" % chess.COLOR_NAMES[us])
    root = _ordered(state, us, width)
    if not root:
        raise ValueError("no legal placement for %s" % chess.COLOR_NAMES[us])

    for depth in range(1, max_depth + 1):
        best, best_score = None, -MATE_SCORE * 2
        alpha = -MATE_SCORE * 2
        for pt, sq in root:
            snap = _make(state, pt, sq)
            try:
                v = _ab(state, us, depth - 1, alpha, MATE_SCORE * 2, width)
            finally:
                _unmake(state, sq, snap)
            if v > best_score:
                best, best_score = (pt, sq), v
                if v > alpha:
                    alpha = v
        # Search the previous best first next time round: it is usually still
        # best, which is what makes the alpha-beta cutoffs pay.
        root.sort(key=lambda m: m == best, reverse=True)
        yield depth, best, best_score
        if abs(best_score) == MATE_SCORE:
            return


def best(state, us, max_depth=MAX_DEPTH, width=BEAM_WIDTH):
    """Deepest placement within the budget. Convenience over search()."""
    move = None
    for _, move, _ in search(state, us, max_depth, width):
        pass
    return move


def _selfcheck():
    b = chess.Board(None)

    # One attacker on a defended bishop is not a win; the second one is.
    b.set_piece_at(chess.C1, chess.Piece(chess.BISHOP, chess.WHITE))
    b.set_piece_at(chess.B2, chess.Piece(chess.PAWN, chess.WHITE))
    b.set_piece_at(chess.A3, chess.Piece(chess.BISHOP, chess.BLACK))
    assert see(b, chess.C1, chess.BLACK) == 0, see(b, chess.C1, chess.BLACK)
    b.set_piece_at(chess.H6, chess.Piece(chess.BISHOP, chess.BLACK))
    assert see(b, chess.C1, chess.BLACK) == 300, see(b, chess.C1, chess.BLACK)

    # Undefended piece is simply lost.
    b2 = chess.Board(None)
    b2.set_piece_at(chess.D4, chess.Piece(chess.ROOK, chess.WHITE))
    b2.set_piece_at(chess.D8, chess.Piece(chess.ROOK, chess.BLACK))
    assert see(b2, chess.D4, chess.BLACK) == 500

    # A capture that loses material scores zero, not negative: nobody is forced.
    b3 = chess.Board(None)
    b3.set_piece_at(chess.E4, chess.Piece(chess.PAWN, chess.WHITE))
    b3.set_piece_at(chess.D5, chess.Piece(chess.PAWN, chess.WHITE))
    b3.set_piece_at(chess.E7, chess.Piece(chess.QUEEN, chess.BLACK))
    assert see(b3, chess.E4, chess.BLACK) == 100

    # The search must not hang a piece when a safe square exists. Black has a
    # bishop aiming at a1-h8; placing a rook on the diagonal loses it outright.
    st = rules.SetupState()
    st.place(chess.PAWN, chess.A2)
    st.turn = chess.BLACK
    st.place(chess.BISHOP, chess.H8)      # mirrored into black's own half
    st.turn = chess.WHITE
    hung = [sq for sq, p in st.board.piece_map().items()
            if p.color == chess.BLACK]
    assert hung, "black bishop did not land"
    move = best(st, chess.WHITE, max_depth=2, width=8)
    snap = _make(st, *move)
    assert _worst_exchange(st.board, chess.WHITE) == 0, \
        "search hung material on %s" % chess.square_name(move[1])
    _unmake(st, move[1], snap)

    # Iterative deepening reports every depth in order and never regresses to
    # an illegal move.
    st2 = rules.SetupState()
    seen = [d for d, mv, _ in search(st2, chess.WHITE, max_depth=3, width=6)
            if mv in set(st2.legal_placements())]
    assert seen == [1, 2, 3], seen

    # make/unmake must leave the state byte-identical.
    st3 = rules.SetupState()
    before = (st3.board.fen(), dict(st3.points), st3.turn, st3.result)
    snap = _make(st3, chess.QUEEN, chess.D1)
    _unmake(st3, chess.D1, snap)
    assert (st3.board.fen(), dict(st3.points), st3.turn, st3.result) == before

    print("OK: psearch selfcheck passed")


if __name__ == "__main__":
    _selfcheck()
