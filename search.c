/* Alpha-beta search for Setup Chess.
 *
 * Negamax with alpha-beta, iterative deepening, a quiescence search over
 * captures, and MVV-LVA ordering with the previous iteration's best move
 * tried first. Deliberately small: the point of this engine is that it can
 * take positions Stockfish crashes on, not that it out-searches Stockfish.
 *
 * setup: no transposition table, no killers, no history, no null move, no
 * repetition detection. Each is a separate measurable change. Repetition is
 * the one with a correctness flavour -- the engine cannot see that it is
 * repeating -- but the harness adjudicates game results, so it costs strength
 * rather than legality. Upgrade path, roughly in expected-value order: TT,
 * killers, repetition, null move.
 */
#include <string.h>

#include "Constants.h"

int evaluate(const Position *p);
int piece_value(int type);
int gen_legal(const Position *p, Move *list);
void make_move(Position *p, Move m, Undo *u);
void unmake_move(Position *p, Move m, const Undo *u);
int in_check(const Position *p, int color);
int piece_at(const Position *p, int sq, int color);
void init_tables(void);

#define MATE 30000
#define INF 31000
#define MAX_PLY 64

typedef struct {
    uint64_t nodes;
    uint64_t limit;      /* node budget; 0 means unlimited */
    int stop;
} Search;

static int is_capture(const Position *p, Move m)
{
    int to = MV_TO(m);
    if (p->occ[!p->side] & BB(to)) return 1;
    /* en passant: a pawn stepping onto the ep square captures */
    return (to == p->ep && (p->pieces[p->side][PAWN] & BB(MV_FROM(m))));
}

/* Most valuable victim, least valuable attacker. Promotions score high so a
 * queening move is tried before a quiet one. */
static int move_score(const Position *p, Move m)
{
    int score = 0;
    int to = MV_TO(m), from = MV_FROM(m);
    int victim = piece_at(p, to, !p->side);
    if (victim >= 0) {
        int attacker = piece_at(p, from, p->side);
        score = 10000 + piece_value(victim) * 8
                - (attacker >= 0 ? piece_value(attacker) : 0);
    } else if (to == p->ep && (p->pieces[p->side][PAWN] & BB(from))) {
        score = 10000 + piece_value(PAWN) * 8;
    }
    if (MV_PROMO(m)) score += 9000 + piece_value(MV_PROMO(m));
    return score;
}

static void sort_moves(const Position *p, Move *list, int n, Move first)
{
    int scores[MAX_MOVES];
    for (int i = 0; i < n; i++) {
        scores[i] = move_score(p, list[i]);
        if (first && list[i] == first) scores[i] = 1 << 20;
    }
    /* insertion sort: n is small and this keeps ties stable, which keeps the
     * node count reproducible as an oracle */
    for (int i = 1; i < n; i++) {
        Move m = list[i];
        int s = scores[i], j = i - 1;
        while (j >= 0 && scores[j] < s) {
            list[j + 1] = list[j];
            scores[j + 1] = scores[j];
            j--;
        }
        list[j + 1] = m;
        scores[j + 1] = s;
    }
}

static int quiesce(Position *p, int alpha, int beta, Search *s)
{
    if (s->limit && s->nodes >= s->limit) {
        s->stop = 1;
        return evaluate(p);
    }
    s->nodes++;
    int stand = evaluate(p);
    if (stand >= beta) return beta;
    if (stand > alpha) alpha = stand;

    Move list[MAX_MOVES];
    int n = gen_legal(p, list);
    sort_moves(p, list, n, 0);
    for (int i = 0; i < n; i++) {
        if (!is_capture(p, list[i]) && !MV_PROMO(list[i])) continue;
        Undo u;
        make_move(p, list[i], &u);
        int score = -quiesce(p, -beta, -alpha, s);
        unmake_move(p, list[i], &u);
        if (s->stop) return alpha;
        if (score >= beta) return beta;
        if (score > alpha) alpha = score;
    }
    return alpha;
}

static int negamax(Position *p, int depth, int ply, int alpha, int beta,
                   Search *s)
{
    if (s->limit && s->nodes >= s->limit) {
        s->stop = 1;
        return 0;
    }
    if (depth <= 0) return quiesce(p, alpha, beta, s);
    s->nodes++;

    Move list[MAX_MOVES];
    int n = gen_legal(p, list);
    if (n == 0)  /* mate scores prefer the shortest path */
        return in_check(p, p->side) ? -MATE + ply : 0;
    if (p->halfmove >= 100) return 0;

    sort_moves(p, list, n, 0);
    int best = -INF;
    for (int i = 0; i < n; i++) {
        Undo u;
        make_move(p, list[i], &u);
        int score = -negamax(p, depth - 1, ply + 1, -beta, -alpha, s);
        unmake_move(p, list[i], &u);
        if (s->stop) return best > -INF ? best : alpha;
        if (score > best) best = score;
        if (score > alpha) alpha = score;
        if (alpha >= beta) break;
    }
    return best;
}

/* Iterative deepening root. Returns the score; writes the move to *best and
 * the node count to *nodes. A node limit stops the search mid-iteration, in
 * which case the last completed iteration's move is kept. */
int search(Position *p, int max_depth, uint64_t node_limit, Move *best,
           uint64_t *nodes)
{
    init_tables();
    Search s = {0, node_limit, 0};
    Move list[MAX_MOVES];
    int n = gen_legal(p, list);
    *best = n ? list[0] : 0;
    int score = 0;
    if (!n) {
        *nodes = 0;
        return in_check(p, p->side) ? -MATE : 0;
    }
    if (max_depth <= 0 || max_depth > MAX_PLY) max_depth = MAX_PLY;

    Move pv = 0;
    for (int depth = 1; depth <= max_depth; depth++) {
        int alpha = -INF, local_best_score = -INF;
        Move local_best = pv ? pv : list[0];
        sort_moves(p, list, n, pv);
        for (int i = 0; i < n; i++) {
            Undo u;
            make_move(p, list[i], &u);
            int v = -negamax(p, depth - 1, 1, -INF, -alpha, &s);
            unmake_move(p, list[i], &u);
            if (s.stop) break;
            if (v > local_best_score) {
                local_best_score = v;
                local_best = list[i];
            }
            if (v > alpha) alpha = v;
        }
        if (s.stop) break;          /* keep the last finished iteration */
        pv = local_best;
        *best = local_best;
        score = local_best_score;
        if (score > MATE - MAX_PLY || score < -MATE + MAX_PLY) break;
    }
    *nodes = s.nodes;
    return score;
}

int eval_entry(Position *p) { init_tables(); return evaluate(p); }
