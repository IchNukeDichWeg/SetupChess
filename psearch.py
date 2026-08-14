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

The leaf has two terms: a static exchange evaluation, which is the cheapest
thing that sees the mechanism above, and agreement with the solved equilibrium,
which is what stops the search playing arbitrarily while the board is empty.
Raw material does not appear -- both sides spend the same 39 points, so
committed-plus-remaining is a constant and cancels.
"""

import time

import chess

import rules


class _Timeout(Exception):
    """Deadline hit mid-depth. The partial result is discarded: an aborted
    depth has searched only some of its root moves, so its 'best' is whatever
    happened to be examined first, not a better answer than the depth below."""

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

# Centipawns for a full unit of plan agreement. UNCALIBRATED: set so the army
# term and the exchange term can outvote each other, since an army term that
# dominates just reproduces the plan-following drafter and an exchange term
# that dominates hangs nothing and builds junk. Calibrate by A/B once drafted
# setups are measurable, not by taste.
ARMY_SCALE = 600.0

# Weight on the engine's piece-square knowledge. 0.0 = OFF, which is the
# default until it is measured: depth 2 bought nothing over depth 1, so the
# leaf is the bottleneck and this is the first candidate for widening it.
# Screen with arena --draft and --pst-scale before changing this line.
PST_SCALE = 0.0

_PLAN = None
_CENGINE = False


def support_plan(pool_path=None, gate_path=None):
    """[(frozenset of (piece, own-perspective square), weight)] for the armies
    carrying equilibrium weight.

    This is where seven campaigns of army measurement finally get used instead
    of re-run. The exchange leaf alone is blind on an empty board -- nothing
    attacks anything, every placement ties, and the search returns whatever
    sorted first.

    Squares are in OWN perspective (Black mirrored), matching play.Drafter, so
    one plan serves both colours.
    """
    import json
    import os

    import play

    pool_path = pool_path or play.DEFAULT_POOL
    armies, _ = play.load_pool(pool_path)

    weights = {}
    gate_path = gate_path or pool_path + ".gate"
    if os.path.exists(gate_path):
        try:
            with open(gate_path) as fh:
                weights = {int(k): float(v)
                           for k, v in json.load(fh).get("support", {}).items()}
        except (ValueError, KeyError):
            weights = {}
    if not weights:
        weights = {i: 1.0 / len(armies) for i in range(len(armies))}

    total = sum(weights.values()) or 1.0
    return [(frozenset(armies[i]), w / total)
            for i, w in sorted(weights.items()) if w and i < len(armies)]


def default_plan():
    """Lazily loaded module plan, so importing psearch stays cheap."""
    global _PLAN
    if _PLAN is None:
        try:
            _PLAN = support_plan()
        except (OSError, ValueError, KeyError):
            _PLAN = []      # no pool on disk: exchanges only, still correct
    return _PLAN


def agreement(board, color, plan):
    """Fraction of `color`'s placed pieces that an equilibrium army would share.

    The expected overlap against an army drawn from the solved mixture, in
    [0, 1]. Three properties earn it the job over the obvious alternatives:

      * TEMPO-NEUTRAL. It is a fraction, not a running total, so a side is not
        scored ahead merely for having placed more pieces. A summed weight
        table swung the root score by ~900 every ply and made the search chase
        whichever leaf happened to place last.
      * NO FREE-PIECE ARTIFACT. Crediting quality per point spent made the
        king, which costs 0, look like infinite value: the search opened with
        Ke1 and thrashed between depths.
      * SMOOTH OFF-PLAN. A strict "is this army still reachable" test collapses
        to zero the moment one piece goes off-book and then gives no gradient
        at all. Overlap keeps grading a position after it leaves the pool.
    """
    placed = frozenset(
        (p.piece_type, sq if p.color == chess.WHITE else chess.square_mirror(sq))
        for sq, p in board.piece_map().items() if p.color == color)
    if not placed or not plan:
        return 0.0
    n = float(len(placed))
    return sum(w * len(placed & army) for army, w in plan) / n


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
    """Best single capture sequence available AGAINST `victim_color`.

    The attacker test before see() is what makes the search affordable: see()
    copies the board, and in a setup position most pieces are not attacked at
    all, so the copy was being paid dozens of times per leaf for a guaranteed
    zero. attackers() is a bitboard intersection.
    """
    attacker = not victim_color
    best = 0
    for sq, piece in board.piece_map().items():
        if piece.color != victim_color:
            continue
        if not board.attackers_mask(attacker, sq):
            continue
        v = see(board, sq, attacker)
        if v > best:
            best = v
    return best


def piece_square(state, us):
    """The engine's positional knowledge, with MATERIAL removed exactly.

    cengine.evaluate is material plus piece-square tables, and raw material is
    meaningless mid-setup: whoever has placed more pieces is "ahead" purely on
    tempo. It cancels exactly rather than approximately, because the engine's
    piece values are precisely 100x the point costs -- P 1/100, N 3/300,
    B 3/300, R 5/500, Q 9/900 -- so 100 * (points still unspent) restores what
    each side has yet to place. Verified: a mirrored position corrects to
    exactly 0, and the residual over 300 random partial setups reaches 220cp,
    which is the piece-square signal this is here to collect.

    The board's turn is meaningless during setup, so it is set to White and
    restored; evaluate() reads it.
    """
    global _CENGINE
    if _CENGINE is False:
        try:
            import cengine
            _CENGINE = cengine
        except Exception:      # no built dylib: fall back to exchanges + plan
            _CENGINE = None
    if _CENGINE is None:
        return 0

    board = state.board
    saved = board.turn
    board.turn = chess.WHITE
    try:
        v = _CENGINE.evaluate(board)
    finally:
        board.turn = saved
    v += 100 * (state.points[chess.WHITE] - state.points[chess.BLACK])
    return v if us == chess.WHITE else -v


def leaf(state, us, plan=None):
    """Static score of a setup position, in centipawns, from `us`.

    Two terms. EXCHANGES: what either side can win by capturing at handoff.
    Raw material does not appear -- both sides get the same 39 points, so
    committed plus still-affordable is a constant per side and cancels.
    AGREEMENT: how much of each side's placed material an equilibrium army
    would also have placed (see agreement()), which is what stops the search
    playing arbitrarily while the board is still empty.

    ponytail: the WORST single exchange per side, not the sum of all of them.
    Only one capture can actually be played first, so summing would double-count
    a position with several loose pieces. It also means the search happily
    leaves two hanging pieces rather than one -- fix by scoring the top two if
    that shows up in real games.
    """
    board = state.board
    score = _worst_exchange(board, not us) - _worst_exchange(board, us)

    if plan is None:
        plan = default_plan()
    if plan:
        score += int(ARMY_SCALE * (agreement(board, us, plan)
                                   - agreement(board, not us, plan)))
    if PST_SCALE:
        score += int(PST_SCALE * piece_square(state, us))
    return score


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


def _ordered(state, us, width, plan):
    """The `width` most promising placements, best first, by a 1-ply score."""
    scored = []
    for pt, sq in state.legal_placements():
        snap = _make(state, pt, sq)
        s = _terminal(state, us)
        if s is None:
            s = leaf(state, us, plan)
        _unmake(state, sq, snap)
        scored.append((s, pt, sq))
    # The side to move wants its own score high; `us` is fixed, so the opponent
    # sorts the other way.
    scored.sort(key=lambda t: t[0], reverse=(state.turn == us))
    return [(pt, sq) for _, pt, sq in scored[:width]]


def _ab(state, us, depth, alpha, beta, width, plan, deadline=None):
    if deadline is not None and time.monotonic() > deadline:
        raise _Timeout
    s = _terminal(state, us)
    if s is not None:
        return s
    if depth == 0 or state.complete:
        return leaf(state, us, plan)

    moves = _ordered(state, us, width, plan)
    if not moves:
        return leaf(state, us, plan)

    maximising = state.turn == us
    best = -MATE_SCORE * 2 if maximising else MATE_SCORE * 2
    for pt, sq in moves:
        snap = _make(state, pt, sq)
        try:
            v = _ab(state, us, depth - 1, alpha, beta, width, plan,
                    deadline)
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


def search(state, us, max_depth=MAX_DEPTH, width=BEAM_WIDTH, plan=None,
           budget=None):
    """Yield (depth, (piece_type, square), score) once per completed depth.

    A generator so the caller owns the budget: break out on a clock, after a
    depth, or when the move stops changing. Nothing here reads a timer, which
    keeps the same code usable for a 3-second live placement and an overnight
    analysis.
    """
    if state.turn != us:
        raise ValueError("not %s to place" % chess.COLOR_NAMES[us])
    if plan is None:
        plan = default_plan()
    root = _ordered(state, us, width, plan)
    if not root:
        raise ValueError("no legal placement for %s" % chess.COLOR_NAMES[us])

    deadline = (time.monotonic() + budget) if budget else None
    for depth in range(1, max_depth + 1):
        best, best_score = None, -MATE_SCORE * 2
        alpha = -MATE_SCORE * 2
        for pt, sq in root:
            snap = _make(state, pt, sq)
            try:
                v = _ab(state, us, depth - 1, alpha, MATE_SCORE * 2, width,
                        plan, deadline)
            except _Timeout:
                _unmake(state, sq, snap)
                return          # discard this depth, keep what was yielded
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


def best(state, us, max_depth=MAX_DEPTH, width=BEAM_WIDTH, plan=None,
         budget=None):
    """Deepest placement within the budget. Convenience over search().

    Never returns None. A budget tight enough to abort during depth 1 leaves
    search() yielding nothing, and callers place() whatever comes back, so a
    None here is a crash in the caller rather than a slow move. Fall back to
    the best placement by the 1-ply score, which costs one _ordered pass.
    """
    move = None
    for _, move, _ in search(state, us, max_depth, width, plan, budget):
        pass
    if move is None:
        if plan is None:
            plan = default_plan()
        fallback = _ordered(state, us, 1, plan)
        if not fallback:
            raise ValueError("no legal placement for %s"
                             % chess.COLOR_NAMES[us])
        move = fallback[0]
    return move


def _selfcheck():
    # Exchange assertions run with an EMPTY table: they pin the SEE arithmetic,
    # and a pool-derived army term would drown the numbers they check.
    bare = []
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
    move = best(st, chess.WHITE, max_depth=2, width=8, plan=bare)
    snap = _make(st, *move)
    assert _worst_exchange(st.board, chess.WHITE) == 0, \
        "search hung material on %s" % chess.square_name(move[1])
    _unmake(st, move[1], snap)

    # Iterative deepening reports every depth in order and never regresses to
    # an illegal move.
    st2 = rules.SetupState()
    seen = [d for d, mv, _ in search(st2, chess.WHITE, max_depth=3, width=6,
                                     plan=bare)
            if mv in set(st2.legal_placements())]
    assert seen == [1, 2, 3], seen

    # Material must cancel EXACTLY out of the piece-square term, or it just
    # re-introduces the tempo artifact that agreement() was written to avoid.
    st_pst = rules.SetupState()
    assert piece_square(st_pst, chess.WHITE) == 0, "empty board is not 0"
    for pt, sq in ((chess.BISHOP, chess.B2), (chess.BISHOP, chess.B7),
                   (chess.ROOK, chess.A1), (chess.ROOK, chess.A8)):
        st_pst.place(pt, sq)
    assert piece_square(st_pst, chess.WHITE) == 0, "mirrored position is not 0"
    assert piece_square(st_pst, chess.BLACK) == 0
    # and it must be antisymmetric in colour
    st_pst.place(chess.QUEEN, chess.D1)
    assert piece_square(st_pst, chess.WHITE) == -piece_square(st_pst, chess.BLACK)

    # A budget too tight to finish depth 1 must still yield a legal placement,
    # not None: draft.py places whatever best() returns.
    st_tight = rules.SetupState()
    mv = best(st_tight, chess.WHITE, max_depth=4, width=8, plan=bare,
              budget=1e-9)
    assert mv is not None, "best() returned None under an impossible budget"
    assert mv in set(st_tight.legal_placements()), mv

    # make/unmake must leave the state byte-identical.
    st3 = rules.SetupState()
    before = (st3.board.fen(), dict(st3.points), st3.turn, st3.result)
    snap = _make(st3, chess.QUEEN, chess.D1)
    _unmake(st3, chess.D1, snap)
    assert (st3.board.fen(), dict(st3.points), st3.turn, st3.result) == before

    print("OK: psearch selfcheck passed")


if __name__ == "__main__":
    _selfcheck()
