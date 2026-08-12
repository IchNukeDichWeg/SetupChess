"""Setup Chess drafting rules: placement legality, points, FEN handoff.

Implements the placement game specified in docs/RULES.md (chess.com Setup
Chess, verified 2026-08-04): 39 points, P1 N3 B3 R5 Q9 K0, pieces on your
first three ranks, pawns only on ranks 2-3, one free mandatory king,
alternating visible placement with White first, in-setup check/checkmate,
no castling after setup.

An "army" is one side's completed placement list [(piece_type, square), ...]
always expressed from White's perspective; mirror_army() flips it for Black.
"""

import chess

BUDGET = 39
PIECE_COST = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}
BUYABLE = (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)


def zone_ranks(color):
    return (0, 1, 2) if color == chess.WHITE else (5, 6, 7)


def pawn_ranks(color):
    return (1, 2) if color == chess.WHITE else (5, 6)


def placement_squares(piece_type, color):
    ranks = pawn_ranks(color) if piece_type == chess.PAWN else zone_ranks(color)
    for r in ranks:
        for f in range(8):
            yield chess.square(f, r)


def army_cost(army):
    return sum(PIECE_COST[pt] for pt, _ in army)


def mirror_army(army):
    return [(pt, chess.square_mirror(sq)) for pt, sq in army]


def validate_army(army, color=chess.WHITE):
    """Static check of one side's completed army. Returns (ok, reason)."""
    seen = set()
    kings = 0
    cost = 0
    for pt, sq in army:
        if pt != chess.KING and pt not in BUYABLE:
            return False, "unknown piece type %r" % (pt,)
        if sq in seen:
            return False, "duplicate square " + chess.square_name(sq)
        seen.add(sq)
        if pt == chess.KING:
            kings += 1
        cost += PIECE_COST[pt]
        ranks = pawn_ranks(color) if pt == chess.PAWN else zone_ranks(color)
        if chess.square_rank(sq) not in ranks:
            return False, "%s on illegal square %s" % (
                chess.piece_name(pt), chess.square_name(sq))
    if kings != 1:
        return False, "%d kings" % kings
    if cost > BUDGET:
        return False, "cost %d exceeds budget %d" % (cost, BUDGET)
    return True, ""


def setup_fen(white_army, black_army):
    """Handoff FEN for two completed armies, each in its own perspective.

    No castling, no en passant - per docs/RULES.md this is an ordinary chess
    position (rank-3 pawns lose their double-step in normal chess anyway).
    Run the result through validate_fen before any engine.

    White to move BY CONVENTION, which is a modelling choice, not the rule:
    in a real game whoever follows the final placement moves first (see
    SetupState.handoff_fen). Two finished armies carry no placement order, so
    there is nothing to derive it from. Every caller here is a pairwise
    matchup that plays both colours, so the free tempo goes to each side once
    and cancels in the pair. Use handoff_fen for anything driving a real game.
    """
    board = chess.Board(None)
    for pt, sq in white_army:
        board.set_piece_at(sq, chess.Piece(pt, chess.WHITE))
    for pt, sq in mirror_army(black_army):
        board.set_piece_at(sq, chess.Piece(pt, chess.BLACK))
    board.turn = chess.WHITE
    return board.fen()


# python-chess judges a FEN by ordinary-chess history, which Setup Chess
# positions do not have: 16 pawns, 20 pieces or four queens a side are all
# reachable inside the 39 points, and two black pieces can be checking White
# at handoff. Stockfish plays every one of them (verified: the 16-pawn wall
# above returns e2e4 at +17.36 rather than erroring), so these flags are
# noise here. Everything NOT on this list still rejects, because the rest
# genuinely breaks an engine or cannot arise from a legal setup.
SETUP_LEGAL_STATUS = (
    chess.STATUS_TOO_MANY_WHITE_PAWNS | chess.STATUS_TOO_MANY_BLACK_PAWNS
    | chess.STATUS_TOO_MANY_WHITE_PIECES | chess.STATUS_TOO_MANY_BLACK_PIECES
    | chess.STATUS_TOO_MANY_CHECKERS | chess.STATUS_IMPOSSIBLE_CHECK
)


def validate_fen(fen):
    """Is this FEN safe to hand to a UCI engine? Returns (ok, reason).

    Setup-Chess-legal oddities (pawn and piece counts above the standard
    army, multi-piece check at handoff) pass; pawns on a back rank, missing
    or duplicate kings, castling rights no rook supports, a bad en passant
    square and a non-mover left in check all still fail.
    """
    try:
        board = chess.Board(fen)
    except ValueError as e:
        return False, "unparseable: %s" % e
    status = board.status() & ~SETUP_LEGAL_STATUS
    if status == chess.STATUS_VALID:
        return True, ""
    reasons = [s.name.lower() for s in chess.Status if s and status & s]
    return False, ", ".join(reasons)


# Stockfish segfaults on high piece counts once the search gets going: the
# pawn-wall mirror (42 pieces) answers depth 4 and 600 nodes fine and then
# dies with SIGSEGV at 20,000. Measured threshold on Homebrew Stockfish,
# arm64 macOS: 36 pieces survive, 38 crash. The guard sits at the ordinary
# chess maximum of 32, which is the count the data structures are actually
# designed for, rather than at the empirical cliff which is one build away
# from moving. Our own C core has to handle up to 48 (24 placeable squares
# a side), which is a reason to start it on a hand-crafted eval: an NNUE
# trained on normal material is exactly what breaks here.
ENGINE_MAX_PIECES = 32


def engine_safe(fen, max_pieces=None):
    """Can THIS engine be trusted with this position?

    Separate from validate_fen: these positions are perfectly legal Setup
    Chess, they just exceed what an ordinary engine's data structures expect.
    The ceiling is a property of the engine, not of the variant, so callers
    running our own core pass a higher one (or 0 for none) -- otherwise the
    high-piece-count cells stay unmeasured even by the engine built to
    measure them. Returns (ok, reason).
    """
    ok, why = validate_fen(fen)
    if not ok:
        return False, why
    cap = ENGINE_MAX_PIECES if max_pieces is None else max_pieces
    if cap <= 0:
        return True, ""
    n = len(chess.Board(fen).piece_map())
    if n > cap:
        return False, "%d pieces exceeds the %d this engine can be trusted with" % (
            n, cap)
    return True, ""


class SetupState:
    """The alternating placement game. White places first (docs/RULES.md).

    A placement is legal iff the square is empty, in the placer's zone
    (pawn-restricted for pawns), affordable, and does not leave the placer's
    own king attacked - which in one rule covers "must block a setup check"
    and "may not place the king onto an attacked square".

    Terminal states (see docs/RULES.md "setup-phase tactics"): a checked
    side that cannot answer with a placement loses immediately (observed
    live: @Qe1+#) - unless the checker was finished too, in which case see
    mates(). `result` is None while the setup runs, else "1-0"/"0-1".
    """

    def __init__(self):
        self.board = chess.Board(None)
        self.points = {chess.WHITE: BUDGET, chess.BLACK: BUDGET}
        self.turn = chess.WHITE
        self.result = None

    def done(self, color):
        """Out of resources: king placed and nothing more to spend/place.

        Unspent points are forfeited when no affordable square is left
        (engine-code confirmed, docs/RULES.md). Check pressure is routed
        explicitly in place() and mates(), not here.
        """
        if self.board.king(color) is None:
            return False
        return self.points[color] == 0 or not self._placements(color)

    @property
    def complete(self):
        return (self.result is None
                and self.done(chess.WHITE) and self.done(chess.BLACK))

    def in_check(self, color):
        k = self.board.king(color)
        return k is not None and self.board.is_attacked_by(not color, k)

    def _placements(self, color):
        out = []
        # `is None`, not truthiness: a king on a1 is square 0
        has_king = self.board.king(color) is not None
        types = BUYABLE if has_king else BUYABLE + (chess.KING,)
        for pt in types:
            if PIECE_COST[pt] > self.points[color]:
                continue
            for sq in placement_squares(pt, color):
                if self.board.piece_at(sq):
                    continue
                self.board.set_piece_at(sq, chess.Piece(pt, color))
                ok = not self.in_check(color)
                self.board.remove_piece_at(sq)
                if ok:
                    out.append((pt, sq))
        return out

    def legal_placements(self):
        return self._placements(self.turn)

    def mates(self, placer):
        """Has placer's placement just ended the setup? Piece already down.

        Assumes the piece is on the board AND its cost already deducted --
        both callers arrange that, play.py's probe by deducting and restoring
        around the call.

        A check the opponent cannot answer with a placement is mate, EXCEPT
        when the placer is finished too: the setup ends here, so the chess
        phase starts with the checked side to move (the turn rule verified
        [LIVE], see place()) and it simply walks the king out.

        ASSUMPTION, and unobserved either way. The live setup mate [AB:
        @Qe1+#] was against a side whose opponent still had points to spend,
        so it does not settle this. The previous form of the exception was
        White-specific, on the belief that White always has the first move
        after handoff; that turned out to be false, so the condition is now
        the reason itself, and it applies to both colours.
        """
        opp = not placer
        if not self.in_check(opp) or self._placements(opp):
            return False
        return not self.done(placer)

    def place(self, pt, sq):
        if self.result is not None:
            raise ValueError("setup already decided %s" % self.result)
        if (pt, sq) not in self.legal_placements():
            raise ValueError("illegal placement %s%s for %s" % (
                chess.piece_symbol(pt), chess.square_name(sq),
                chess.COLOR_NAMES[self.turn]))
        placer = self.turn
        opp = not placer
        self.board.set_piece_at(sq, chess.Piece(pt, placer))
        self.points[placer] -= PIECE_COST[pt]
        if self.in_check(opp):
            if self._placements(opp):
                self.turn = opp  # forced to answer the check
                return
            if self.mates(placer):
                self.result = "1-0" if placer == chess.WHITE else "0-1"
                return
        # LOCKED OUT OF A KING. docs/RULES.md records ending setup without a
        # king as an outright loss ("failed to set up his king" in the shipped
        # client), and HUNT_WHEN exists precisely to force it -- but nothing
        # ever SCORED it. done() returns False for a kingless side, so
        # `complete` never became True, the turn was handed back forever to a
        # side with no legal placement, and the designed win deadlocked:
        # Drafter.choose raised ValueError, handoff_fen raised "setup not
        # complete", live() rejected every further token, and match.py caught
        # the ValueError and discarded a WON game as an engine error.
        #
        # Checked for both sides: the placer can also spend its last points
        # without a king, which is the same loss by the same rule.
        for side in (opp, placer):
            if self.board.king(side) is None and not self._placements(side):
                self.result = "0-1" if side == chess.WHITE else "1-0"
                return

        # Strict alternation, always. A side with nothing left to place PASSES
        # rather than being skipped, which is exactly what the server records:
        # a live game showed "11. @Bf3 P / 12. @Bg2 P / ..." with White
        # placing alone and Black passing on every one of its turns.
        #
        # The parity this produces is load-bearing. When both sides are done
        # the turn still advances, so the CHESS PHASE STARTS WITH THE SIDE
        # AFTER THE LAST PLACEMENT -- not with White. Observed live: White
        # placed 16th and last, and Black opened the game with Qh3+.
        self.turn = opp
        if not self.complete and self.done(opp):
            self.turn = placer          # they pass, we place again

    def handoff_fen(self):
        """The position to hand an engine, with the correct side to move.

        NOT always White. See place(): the placement turns keep alternating
        through the passes, so whoever follows the final placement moves
        first. Getting this wrong hands the engine a position a tempo out of
        step, which is how a real game was lost before this was measured.
        """
        if not self.complete:
            raise ValueError("setup not complete")
        self.board.turn = self.turn
        return self.board.fen()
