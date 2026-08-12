/* Hand-crafted evaluation for Setup Chess.
 *
 * Hand-crafted rather than NNUE on purpose. A net trained on ordinary chess
 * material is exactly what breaks here: nine bishops and sixteen pawns are
 * legal, and Stockfish's NNUE build segfaults on a 42-piece position once the
 * search runs. A material-plus-tables eval has no such assumption -- it just
 * sums over whatever is on the board.
 *
 * Two standard terms are deliberately absent:
 *   - the bishop pair bonus, which is meaningless when a side owns nine
 *   - any doubled/isolated pawn structure term, untested in a variant where
 *     a player may buy sixteen pawns and stack them two deep
 * Both are candidates, but each has to be measured on its own.
 *
 * setup: material and piece-square tables only. Mobility is the obvious next
 * term given how many sliders these armies field; it gets added and measured
 * separately, never bundled.
 */
#include "Constants.h"

/* Centipawns. These are PLAY values, not the variant's purchase costs: a
 * bishop costs 3 points like a knight but plays like a bishop. The measured
 * dominance of bishop swarms says bishops are underpriced at 3, not that a
 * bishop is worth more than this in a game. */
static const int VALUE[NPIECES] = {100, 320, 330, 500, 900, 0};

/* Tables are written from White's point of view with rank 1 at the BOTTOM,
 * so index 0 here is a1. Black reads them mirrored. */
static const int PST[NPIECES][64] = {
    /* pawn: push, take the centre, do not bother with the home rank */
    {  0,  0,  0,  0,  0,  0,  0,  0,
       5, 10, 10,-20,-20, 10, 10,  5,
       5, -5,-10,  0,  0,-10, -5,  5,
       0,  0,  0, 20, 20,  0,  0,  0,
       5,  5, 10, 25, 25, 10,  5,  5,
      10, 10, 20, 30, 30, 20, 10, 10,
      50, 50, 50, 50, 50, 50, 50, 50,
       0,  0,  0,  0,  0,  0,  0,  0},
    /* knight: centre, hate the rim */
    {-50,-40,-30,-30,-30,-30,-40,-50,
     -40,-20,  0,  5,  5,  0,-20,-40,
     -30,  5, 10, 15, 15, 10,  5,-30,
     -30,  0, 15, 20, 20, 15,  0,-30,
     -30,  5, 15, 20, 20, 15,  5,-30,
     -30,  0, 10, 15, 15, 10,  0,-30,
     -40,-20,  0,  0,  0,  0,-20,-40,
     -50,-40,-30,-30,-30,-30,-40,-50},
    /* bishop: long diagonals. Flat-ish on purpose -- with up to nine of them
     * a steep table would pile them all onto the same few squares. */
    {-20,-10,-10,-10,-10,-10,-10,-20,
     -10,  5,  0,  0,  0,  0,  5,-10,
     -10, 10, 10, 10, 10, 10, 10,-10,
     -10,  0, 10, 10, 10, 10,  0,-10,
     -10,  5,  5, 10, 10,  5,  5,-10,
     -10,  0,  5, 10, 10,  5,  0,-10,
     -10,  0,  0,  0,  0,  0,  0,-10,
     -20,-10,-10,-10,-10,-10,-10,-20},
    /* rook: open-ish files and the seventh */
    {  0,  0,  5, 10, 10,  5,  0,  0,
      -5,  0,  0,  0,  0,  0,  0, -5,
      -5,  0,  0,  0,  0,  0,  0, -5,
      -5,  0,  0,  0,  0,  0,  0, -5,
      -5,  0,  0,  0,  0,  0,  0, -5,
      -5,  0,  0,  0,  0,  0,  0, -5,
       5, 10, 10, 10, 10, 10, 10,  5,
       0,  0,  0,  0,  0,  0,  0,  0},
    /* queen. LEFT-RIGHT MIRRORED, deliberately. The classic simplified-eval
     * table is asymmetric on three ranks and was copied with that intact:
     * c2 scored 5 against f2 0, b3 5 against g3 0, a4 0 against h4 -5. Six
     * squares in all, measured through cengine.evaluate at 925 against 920.
     * The selftest's symmetry check is a COLOUR mirror, so it passed straight
     * over it. With up to four queens inside 39 points the phantom queenside
     * preference reached about 20 cp. */
    {-20,-10,-10, -5, -5,-10,-10,-20,
     -10,  0,  0,  0,  0,  0,  0,-10,
     -10,  0,  5,  5,  5,  5,  0,-10,
      -5,  0,  5,  5,  5,  5,  0, -5,
      -5,  0,  5,  5,  5,  5,  0, -5,
     -10,  0,  5,  5,  5,  5,  0,-10,
     -10,  0,  0,  0,  0,  0,  0,-10,
     -20,-10,-10, -5, -5,-10,-10,-20},
    /* king: stay home and tucked. No endgame table yet, so this is the one
     * place the eval is knowingly wrong late in a game. */
    { 20, 30, 10,  0,  0, 10, 30, 20,
      20, 20,  0,  0,  0,  0, 20, 20,
     -10,-20,-20,-20,-20,-20,-20,-10,
     -20,-30,-30,-40,-40,-30,-30,-20,
     -30,-40,-40,-50,-50,-40,-40,-30,
     -30,-40,-40,-50,-50,-40,-40,-30,
     -30,-40,-40,-50,-50,-40,-40,-30,
     -30,-40,-40,-50,-50,-40,-40,-30},
};

static int popcount(U64 b) { return __builtin_popcountll(b); }

/* Score from the side-to-move's point of view, as negamax expects. */
int evaluate(const Position *p)
{
    int score = 0;
    for (int t = PAWN; t < NPIECES; t++) {
        U64 w = p->pieces[WHITE][t];
        U64 b = p->pieces[BLACK][t];
        score += VALUE[t] * (popcount(w) - popcount(b));
        while (w) {
            int s = __builtin_ctzll(w);
            w &= w - 1;
            score += PST[t][s];
        }
        while (b) {
            int s = __builtin_ctzll(b);
            b &= b - 1;
            score -= PST[t][s ^ 56];  /* mirror the rank, keep the file */
        }
    }
    return (p->side == WHITE) ? score : -score;
}

int piece_value(int type) { return VALUE[type]; }
