/* Board constants for the Setup Chess core.
 *
 * Bitboards, deliberately: a Setup Chess army can hold up to 24 pieces a side
 * (every square of its three ranks), so 48 on the board against ordinary
 * chess's 32. Piece-list engines size their arrays for 32 and corrupt or
 * crash past it -- that is exactly how Stockfish segfaults on a pawn-wall
 * mirror at 20,000 nodes. A bitboard per (colour, type) has no such ceiling.
 *
 * Castling is absent from the variant entirely (docs/RULES.md), so there are
 * no rights to track and no 960 edge cases. En passant is present.
 */
#ifndef SETUP_CONSTANTS_H
#define SETUP_CONSTANTS_H

#include <stdint.h>

typedef uint64_t U64;

enum { WHITE = 0, BLACK = 1 };
enum { PAWN = 0, KNIGHT, BISHOP, ROOK, QUEEN, KING, NPIECES };

#define SQ(f, r) ((r) * 8 + (f))
#define FILE_OF(s) ((s) & 7)
#define RANK_OF(s) ((s) >> 3)
#define BB(s) (1ULL << (s))

#define NO_SQ 64

/* a move packs from/to/promotion; flags stay minimal because the variant
 * has no castling. */
typedef uint32_t Move;
#define MV_FROM(m) ((m) & 0x3F)
#define MV_TO(m) (((m) >> 6) & 0x3F)
#define MV_PROMO(m) (((m) >> 12) & 0x7) /* 0 = none, else piece enum */
#define MV_MAKE(f, t, p) ((U64)(f) | ((U64)(t) << 6) | ((U64)(p) << 12))

/* 24 placeable squares a side, so a legal position can be far denser than
 * ordinary chess; 256 covers the widest move list such a position can
 * produce with room to spare. */
#define MAX_MOVES 256

typedef struct {
    U64 pieces[2][NPIECES];
    U64 occ[2];
    U64 all;
    int side;
    int ep;        /* en passant target square, or NO_SQ */
    int halfmove;
} Position;

/* undo record for make/unmake */
typedef struct {
    int captured;  /* piece type or -1 */
    int cap_sq;
    int ep;
    int halfmove;
} Undo;

#endif
