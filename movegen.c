/* Move generation and perft for Setup Chess.
 *
 * Ordinary chess rules apply once both armies are placed, minus castling
 * (docs/RULES.md), so this is a normal generator over a position that may
 * hold up to 48 pieces and nine queens a side.
 *
 * Sliding attacks are computed by walking rays. That is the slow way and it
 * is deliberate for now:
 *   setup: ray walk -- swap for magics once perft is the bottleneck rather
 *   than the thing being verified.
 * Correctness first, because the whole point of this core is to be trusted
 * on material Stockfish cannot take.
 */
#include <string.h>
#include <stdlib.h>

#include "Constants.h"

static U64 KNIGHT_ATT[64];
static U64 KING_ATT[64];
static int RAYS[8][2] = {{1, 0}, {-1, 0}, {0, 1}, {0, -1},
                         {1, 1}, {1, -1}, {-1, 1}, {-1, -1}};

static int inited = 0;

void init_tables(void)
{
    if (inited) return;
    inited = 1;
    static const int kn[8][2] = {{1, 2}, {2, 1}, {2, -1}, {1, -2},
                                 {-1, -2}, {-2, -1}, {-2, 1}, {-1, 2}};
    for (int s = 0; s < 64; s++) {
        int f = FILE_OF(s), r = RANK_OF(s);
        U64 n = 0, k = 0;
        for (int i = 0; i < 8; i++) {
            int nf = f + kn[i][0], nr = r + kn[i][1];
            if (nf >= 0 && nf < 8 && nr >= 0 && nr < 8) n |= BB(SQ(nf, nr));
            int kf = f + RAYS[i][0], kr = r + RAYS[i][1];
            if (kf >= 0 && kf < 8 && kr >= 0 && kr < 8) k |= BB(SQ(kf, kr));
        }
        KNIGHT_ATT[s] = n;
        KING_ATT[s] = k;
    }
}

static U64 ray_attacks(int sq, U64 occ, int first, int count)
{
    U64 out = 0;
    int f = FILE_OF(sq), r = RANK_OF(sq);
    for (int d = first; d < first + count; d++) {
        int nf = f, nr = r;
        for (;;) {
            nf += RAYS[d][0];
            nr += RAYS[d][1];
            if (nf < 0 || nf > 7 || nr < 0 || nr > 7) break;
            int t = SQ(nf, nr);
            out |= BB(t);
            if (occ & BB(t)) break;
        }
    }
    return out;
}

static U64 rook_attacks(int sq, U64 occ) { return ray_attacks(sq, occ, 0, 4); }
static U64 bishop_attacks(int sq, U64 occ) { return ray_attacks(sq, occ, 4, 4); }
static U64 queen_attacks(int sq, U64 occ)
{
    return rook_attacks(sq, occ) | bishop_attacks(sq, occ);
}

static int lsb(U64 b) { return __builtin_ctzll(b); }

int piece_at(const Position *p, int sq, int color)
{
    U64 b = BB(sq);
    for (int t = PAWN; t < NPIECES; t++)
        if (p->pieces[color][t] & b) return t;
    return -1;
}

/* Is `sq` attacked by `by`? Used for legality and check detection. */
int attacked(const Position *p, int sq, int by)
{
    U64 pawns = p->pieces[by][PAWN];
    /* a white pawn on x attacks x+7/x+9; invert to test from the target */
    if (by == WHITE) {
        if (FILE_OF(sq) > 0 && sq >= 9 && (pawns & BB(sq - 9))) return 1;
        if (FILE_OF(sq) < 7 && sq >= 7 && (pawns & BB(sq - 7))) return 1;
    } else {
        if (FILE_OF(sq) < 7 && sq + 9 < 64 && (pawns & BB(sq + 9))) return 1;
        if (FILE_OF(sq) > 0 && sq + 7 < 64 && (pawns & BB(sq + 7))) return 1;
    }
    if (KNIGHT_ATT[sq] & p->pieces[by][KNIGHT]) return 1;
    if (KING_ATT[sq] & p->pieces[by][KING]) return 1;
    U64 bq = p->pieces[by][BISHOP] | p->pieces[by][QUEEN];
    if (bishop_attacks(sq, p->all) & bq) return 1;
    U64 rq = p->pieces[by][ROOK] | p->pieces[by][QUEEN];
    if (rook_attacks(sq, p->all) & rq) return 1;
    return 0;
}

int in_check(const Position *p, int color)
{
    /* init_tables() like every sibling entry point. Without it a process
     * whose FIRST C call is in_check() read all-zero KNIGHT_ATT/KING_ATT and
     * returned false for a real knight check, becoming correct only after
     * some other call warmed the tables. No in-repo caller hits it cold, but
     * the bound public API was wrong for any new harness that does. */
    init_tables();
    U64 k = p->pieces[color][KING];
    if (!k) return 0;
    return attacked(p, lsb(k), !color);
}

static void add_move(Move *list, int *n, int from, int to, int promo)
{
    if (*n < MAX_MOVES) list[(*n)++] = MV_MAKE(from, to, promo);
}

static void add_pawn_moves(Move *list, int *n, int from, int to, int color)
{
    int r = RANK_OF(to);
    if ((color == WHITE && r == 7) || (color == BLACK && r == 0)) {
        add_move(list, n, from, to, QUEEN);
        add_move(list, n, from, to, ROOK);
        add_move(list, n, from, to, BISHOP);
        add_move(list, n, from, to, KNIGHT);
    } else {
        add_move(list, n, from, to, 0);
    }
}

int gen_pseudo(const Position *p, Move *list)
{
    init_tables();
    int n = 0, us = p->side, them = !us;
    U64 own = p->occ[us], opp = p->occ[them], occ = p->all;

    /* pawns: single, double from the home rank only (a pawn placed on rank 3
     * gets no double step, docs/RULES.md), captures, en passant */
    U64 pawns = p->pieces[us][PAWN];
    while (pawns) {
        int from = lsb(pawns);
        pawns &= pawns - 1;
        int fwd = (us == WHITE) ? 8 : -8;
        int one = from + fwd;
        if (one >= 0 && one < 64 && !(occ & BB(one))) {
            add_pawn_moves(list, &n, from, one, us);
            int home = (us == WHITE) ? 1 : 6;
            int two = one + fwd;
            if (RANK_OF(from) == home && !(occ & BB(two)))
                add_move(list, &n, from, two, 0);
        }
        for (int df = -1; df <= 1; df += 2) {
            int nf = FILE_OF(from) + df, nr = RANK_OF(from) + (us == WHITE ? 1 : -1);
            if (nf < 0 || nf > 7 || nr < 0 || nr > 7) continue;
            int to = SQ(nf, nr);
            if (opp & BB(to)) add_pawn_moves(list, &n, from, to, us);
            else if (to == p->ep) add_move(list, &n, from, to, 0);
        }
    }

    U64 bb = p->pieces[us][KNIGHT];
    while (bb) {
        int from = lsb(bb); bb &= bb - 1;
        U64 t = KNIGHT_ATT[from] & ~own;
        while (t) { int to = lsb(t); t &= t - 1; add_move(list, &n, from, to, 0); }
    }
    bb = p->pieces[us][BISHOP];
    while (bb) {
        int from = lsb(bb); bb &= bb - 1;
        U64 t = bishop_attacks(from, occ) & ~own;
        while (t) { int to = lsb(t); t &= t - 1; add_move(list, &n, from, to, 0); }
    }
    bb = p->pieces[us][ROOK];
    while (bb) {
        int from = lsb(bb); bb &= bb - 1;
        U64 t = rook_attacks(from, occ) & ~own;
        while (t) { int to = lsb(t); t &= t - 1; add_move(list, &n, from, to, 0); }
    }
    bb = p->pieces[us][QUEEN];
    while (bb) {
        int from = lsb(bb); bb &= bb - 1;
        U64 t = queen_attacks(from, occ) & ~own;
        while (t) { int to = lsb(t); t &= t - 1; add_move(list, &n, from, to, 0); }
    }
    bb = p->pieces[us][KING];
    while (bb) {
        int from = lsb(bb); bb &= bb - 1;
        U64 t = KING_ATT[from] & ~own;
        while (t) { int to = lsb(t); t &= t - 1; add_move(list, &n, from, to, 0); }
    }
    return n;
}

void make_move(Position *p, Move m, Undo *u)
{
    int from = MV_FROM(m), to = MV_TO(m), promo = MV_PROMO(m);
    int us = p->side, them = !us;
    int moved = piece_at(p, from, us);

    u->ep = p->ep;
    u->halfmove = p->halfmove;
    u->captured = -1;
    u->cap_sq = to;

    int cap_sq = to;
    if (moved == PAWN && to == p->ep && p->ep != NO_SQ) {
        cap_sq = (us == WHITE) ? to - 8 : to + 8;
        u->cap_sq = cap_sq;
    }
    int captured = piece_at(p, cap_sq, them);
    if (captured >= 0) {
        u->captured = captured;
        p->pieces[them][captured] &= ~BB(cap_sq);
        p->occ[them] &= ~BB(cap_sq);
    }

    p->pieces[us][moved] &= ~BB(from);
    p->occ[us] &= ~BB(from);
    int landed = promo ? promo : moved;
    p->pieces[us][landed] |= BB(to);
    p->occ[us] |= BB(to);

    p->ep = NO_SQ;
    if (moved == PAWN && abs(to - from) == 16)
        p->ep = (from + to) / 2;

    p->halfmove = (moved == PAWN || captured >= 0) ? 0 : p->halfmove + 1;
    p->all = p->occ[WHITE] | p->occ[BLACK];
    p->side = them;
}

void unmake_move(Position *p, Move m, const Undo *u)
{
    int from = MV_FROM(m), to = MV_TO(m), promo = MV_PROMO(m);
    int us = !p->side, them = p->side;
    int landed = piece_at(p, to, us);
    int moved = promo ? PAWN : landed;

    p->pieces[us][landed] &= ~BB(to);
    p->occ[us] &= ~BB(to);
    p->pieces[us][moved] |= BB(from);
    p->occ[us] |= BB(from);

    if (u->captured >= 0) {
        p->pieces[them][u->captured] |= BB(u->cap_sq);
        p->occ[them] |= BB(u->cap_sq);
    }
    p->ep = u->ep;
    p->halfmove = u->halfmove;
    p->all = p->occ[WHITE] | p->occ[BLACK];
    p->side = us;
}

int gen_legal(const Position *p, Move *list)
{
    Move pseudo[MAX_MOVES];
    int np = gen_pseudo(p, pseudo), n = 0;
    Position tmp = *p;
    for (int i = 0; i < np; i++) {
        Undo u;
        int us = tmp.side;
        make_move(&tmp, pseudo[i], &u);
        if (!in_check(&tmp, us)) list[n++] = pseudo[i];
        unmake_move(&tmp, pseudo[i], &u);
    }
    return n;
}

uint64_t perft(Position *p, int depth)
{
    if (depth == 0) return 1;
    Move list[MAX_MOVES];
    int n = gen_legal(p, list);
    if (depth == 1) return (uint64_t)n;
    uint64_t total = 0;
    for (int i = 0; i < n; i++) {
        Undo u;
        make_move(p, list[i], &u);
        total += perft(p, depth - 1);
        unmake_move(p, list[i], &u);
    }
    return total;
}

/* --- entry points for the Python harness ------------------------------- */

void pos_clear(Position *p)
{
    memset(p, 0, sizeof(*p));
    p->ep = NO_SQ;
}

void pos_set(Position *p, int color, int type, int sq)
{
    p->pieces[color][type] |= BB(sq);
    p->occ[color] |= BB(sq);
    p->all = p->occ[WHITE] | p->occ[BLACK];
}

void pos_finish(Position *p, int side, int ep)
{
    p->side = side;
    p->ep = ep;
    p->all = p->occ[WHITE] | p->occ[BLACK];
}

int legal_moves(Position *p, Move *out) { init_tables(); return gen_legal(p, out); }

uint64_t perft_entry(Position *p, int depth) { init_tables(); return perft(p, depth); }

int position_size(void) { return (int)sizeof(Position); }
