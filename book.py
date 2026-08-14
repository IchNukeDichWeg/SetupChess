"""Precomputed opening placements, searched offline at a budget live play cannot afford.

The first placement of every game we ever play as White is the SAME decision:
empty board, 39 points each. Searching it live is pure waste, and it is the
single most time-critical move there is, because chess.com forfeits if the
first placement does not arrive in roughly 20 seconds.

Measured over 60 drafted games against varied opponents, counting distinct
positions at each of our decision points:

    our turn 0    1 distinct position     100% book hit
    our turn 1   31                       ~50%
    our turn 3   39                       ~37%
    our turn 7   53                       ~13%

So a book pays completely at turn 0, well for two or three turns after, and
then the opponent's branching washes it out. That is the whole justification
for BOOK_TURNS being small: entries past it cost a deep search each and are
almost never read again.

Generating is a long job -- one deep search per position -- so it writes
incrementally and resumes from whatever is already in the file.

    python3 book.py --out book.json --games 200 --turns 4 --depth 3 --width 16
    python3 relay.py --color white --search --book book.json

ponytail: keyed on the exact position, so it is a transposition table on disk
rather than a real opening book with lines. Fine while it only has to cover
the first few plies; if it ever needs to cover more, key on our own placements
only and let the opponent's differ.
"""

import argparse
import json
import os
import random
import sys

import chess

import play
import psearch
import rules

# How many of OUR placements to cover. See the hit-rate table above: past
# about four the entries stop being read often enough to pay for the search.
BOOK_TURNS = 4


def key(state):
    """Position identity: pieces, both budgets, and whose turn it is.

    The board alone is NOT enough -- the same pieces with different points
    remaining is a different decision, and the pass rule means the side to
    move is not implied by the piece count either.
    """
    return "%s|%d,%d|%d" % (state.board.board_fen(),
                            state.points[chess.WHITE],
                            state.points[chess.BLACK],
                            int(state.turn))


def from_key(k):
    """Rebuild a SetupState from a key, so generation need not keep states."""
    board_fen, points, turn = k.split("|")
    w, b = (int(x) for x in points.split(","))
    state = rules.SetupState()
    state.board = chess.Board(board_fen + " w - - 0 1")
    state.points[chess.WHITE] = w
    state.points[chess.BLACK] = b
    state.turn = bool(int(turn))
    return state


def lookup(book, state):
    """(piece_type, square) or None."""
    hit = book.get(key(state))
    return (hit[0], hit[1]) if hit else None


def load(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path) as fh:
        return json.load(fh)


def collect(pool_path, games, turns, seed=0):
    """Positions we actually face, in the order they are first reached.

    Sampled from real drafted play against varied opponents rather than
    enumerated: enumeration is hopeless (136 legal placements at the root, so
    18k positions at ply 2 and 2.5M at ply 3), and most of that tree is
    positions no opponent ever builds toward.
    """
    armies, _ = play.load_pool(pool_path)
    rng = random.Random(seed)
    order, seen = [], set()
    for _ in range(games):
        mine = armies[rng.randrange(len(armies))]
        opp = armies[rng.randrange(len(armies))]
        for us in (chess.WHITE, chess.BLACK):
            state = rules.SetupState()
            drafters = {chess.WHITE: play.Drafter(mine if us == chess.WHITE
                                                  else opp, chess.WHITE),
                        chess.BLACK: play.Drafter(mine if us == chess.BLACK
                                                  else opp, chess.BLACK)}
            mine_turn = 0
            for _ in range(80):
                if state.complete or state.result:
                    break
                legal = state.legal_placements()
                if not legal:
                    break
                if state.turn == us:
                    if mine_turn >= turns:
                        break
                    k = key(state)
                    if k not in seen:
                        seen.add(k)
                        order.append(k)
                    mine_turn += 1
                state.place(*drafters[state.turn].choose(state))
    return order


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pool", default=play.DEFAULT_POOL)
    ap.add_argument("--games", type=int, default=100,
                    help="sampled games per colour to collect positions from")
    ap.add_argument("--turns", type=int, default=BOOK_TURNS,
                    help="how many of OUR placements to cover (default %d)"
                         % BOOK_TURNS)
    ap.add_argument("--depth", type=int, default=3,
                    help="search depth per entry; offline, so deeper than live")
    ap.add_argument("--width", type=int, default=16,
                    help="beam width per entry; wider than live (default 16)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    book = load(args.out)
    if book:
        print("resuming: %d entries already in %s" % (len(book), args.out))

    print("collecting positions from %d games x 2 colours, %d turns deep..."
          % (args.games, args.turns), flush=True)
    positions = collect(args.pool, args.games, args.turns, args.seed)
    todo = [k for k in positions if k not in book]
    print("%d distinct positions, %d already booked, %d to search"
          % (len(positions), len(positions) - len(todo), len(todo)), flush=True)
    if not todo:
        return

    plan = psearch.default_plan()
    for n, k in enumerate(todo, 1):
        state = from_key(k)
        if not state.legal_placements():
            continue
        move = psearch.best(state, state.turn, max_depth=args.depth,
                            width=args.width, plan=plan)
        book[k] = [int(move[0]), int(move[1])]
        # Write every entry: generation is hours and a machine can die at any
        # point in it. Rewriting the whole file each time is fine at book
        # scale (thousands of entries, not millions).
        tmp = args.out + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(book, fh)
        os.replace(tmp, args.out)
        if n % 10 == 0 or n == len(todo):
            print("  %d/%d searched, %d entries" % (n, len(todo), len(book)),
                  flush=True)

    print("wrote %s: %d entries" % (args.out, len(book)))


def _selfcheck():
    st = rules.SetupState()
    k = key(st)
    back = from_key(k)
    assert key(back) == k, (k, key(back))
    assert back.points == st.points and back.turn == st.turn

    # A key must separate positions the board alone cannot.
    st2 = rules.SetupState()
    st2.points[chess.WHITE] = 20
    assert key(st2) != k, "points do not enter the key"
    st3 = rules.SetupState()
    st3.turn = chess.BLACK
    assert key(st3) != k, "side to move does not enter the key"

    # Round trip through a real position, and a book hit must be legal there.
    st4 = rules.SetupState()
    st4.place(chess.BISHOP, chess.B2)
    st4.place(chess.QUEEN, chess.D8)
    rebuilt = from_key(key(st4))
    assert sorted(rebuilt.board.piece_map()) == sorted(st4.board.piece_map())
    assert rebuilt.points == st4.points and rebuilt.turn == st4.turn
    mv = psearch.best(rebuilt, rebuilt.turn, max_depth=1, width=4)
    bk = {key(st4): [int(mv[0]), int(mv[1])]}
    assert lookup(bk, st4) == mv
    assert lookup(bk, rules.SetupState()) is None
    assert mv in set(st4.legal_placements()), "booked move is not legal"

    print("OK: book selfcheck passed")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selfcheck":
        _selfcheck()
    else:
        main()
