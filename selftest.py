"""Pre-commit checks for the Setup Chess engine. Run: python3 selftest.py

Covers the Phase 1 verification rows of the kickoff spec:
  1. point accounting  - 10k random armies never exceed 39; over-budget rejected
  2. placement legality - every generated army on legal squares, exactly one king
  3. FEN emission       - 1000 random setups accepted by the UCI engine at depth 1
  4. validate_fen       - hand-built illegal FENs rejected with a reason
plus regression tests for the setup-phase game flow, including the
checkmate-during-setup sequence observed on the live analysis board.
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

import chess
import chess.engine

import arena
import pool
import rules


def fail(msg):
    print("FAIL:", msg)
    sys.exit(1)


def random_army(rng):
    """Random legal army in own perspective; spends to zero when it can."""
    zone = [sq for r in rules.zone_ranks(chess.WHITE) for sq in
            (chess.square(f, r) for f in range(8))]
    used = {rng.choice(zone)}
    army = [(chess.KING, next(iter(used)))]
    pts = rules.BUDGET
    while pts > 0:
        options = []
        for pt in rules.BUYABLE:
            if rules.PIECE_COST[pt] > pts:
                continue
            free = [sq for sq in rules.placement_squares(pt, chess.WHITE)
                    if sq not in used]
            if free:
                options.append((pt, free))
        if not options:
            break  # stranded points; validate_army still accepts <= budget
        pt, free = rng.choice(options)
        sq = rng.choice(free)
        used.add(sq)
        army.append((pt, sq))
        pts -= rules.PIECE_COST[pt]
    return army


def test_armies(n, rng):
    for _ in range(n):
        army = random_army(rng)
        ok, why = rules.validate_army(army)
        if not ok:
            fail("random army rejected: %s (%r)" % (why, army))
        if rules.army_cost(army) > rules.BUDGET:
            fail("army over budget: %r" % army)
    bad = [
        ("over budget (5 queens)",
         [(chess.KING, chess.E1)] + [(chess.QUEEN, sq) for sq in
                                     (chess.A1, chess.B1, chess.C1, chess.D1, chess.F1)]),
        ("two kings", [(chess.KING, chess.E1), (chess.KING, chess.D1)]),
        ("no king", [(chess.QUEEN, chess.D1)]),
        ("pawn on rank 1", [(chess.KING, chess.E1), (chess.PAWN, chess.A1)]),
        ("piece on rank 4", [(chess.KING, chess.E1), (chess.QUEEN, chess.A4)]),
        ("duplicate square", [(chess.KING, chess.E1), (chess.QUEEN, chess.D1),
                              (chess.ROOK, chess.D1)]),
    ]
    for label, army in bad:
        ok, why = rules.validate_army(army)
        if ok:
            fail("validate_army accepted %s" % label)
    print("PASS: %d random armies legal and within budget; %d bad armies rejected"
          % (n, len(bad)))


def test_validate_fen():
    cases = [
        ("pawn on rank 1", "8/8/8/8/8/8/4k3/P3K3 w - - 0 1"),
        ("pawn on rank 8", "P3k3/8/8/8/8/8/8/4K3 w - - 0 1"),
        ("two white kings", "4k3/8/8/8/8/8/8/2K1K3 w - - 0 1"),
        ("no black king", "8/8/8/8/8/8/8/4K3 w - - 0 1"),
        ("non-mover in check", "4k3/4Q3/8/8/8/8/8/4K3 w - - 0 1"),
        ("unsupported castling rights", "4k3/8/8/8/8/8/8/4K3 w KQkq - 0 1"),
        ("garbage", "not a fen"),
    ]
    for label, fen in cases:
        ok, why = rules.validate_fen(fen)
        if ok:
            fail("validate_fen accepted %s: %s" % (label, fen))
    # Setup-Chess-legal but not standard-chess-legal: these must PASS, since
    # Stockfish plays them and the 39 points genuinely reach them.
    legal = [
        ("plain position", "4k3/pppp4/8/8/8/8/PPPP4/4K3 w - - 0 1"),
        ("16 black pawns",
         "rn1qk2r/pppppppp/pppppppp/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1"),
        ("four queens a side",
         "qqqqk3/8/8/8/8/8/8/QQQQK3 w - - 0 1"),
        ("White in check from two black rooks at handoff",
         "4k3/8/8/8/8/8/8/r3K2r w - - 0 1"),
    ]
    for label, fen in legal:
        ok, why = rules.validate_fen(fen)
        if not ok:
            fail("validate_fen rejected a legal setup (%s): %s" % (label, why))
    # engine_safe is stricter than validate_fen: legal Setup Chess positions
    # with too many pieces crash Stockfish once the search runs (the pawn-wall
    # mirror below segfaults at 20,000 nodes but answers depth 4 fine).
    wall = "rn1qk2r/pppppppp/pppppppp/8/8/PPPPPPPP/PPPPPPPP/RN1QK2R w - - 0 1"
    if not rules.validate_fen(wall)[0]:
        fail("validate_fen rejected the pawn-wall mirror")
    ok, why = rules.engine_safe(wall)
    if ok:
        fail("engine_safe accepted a %d-piece position" % 42)
    if "42 pieces" not in why:
        fail("engine_safe reason lacks the piece count: %s" % why)
    if not rules.engine_safe(legal[0][1])[0]:
        fail("engine_safe rejected an ordinary position")
    print("PASS: validate_fen rejected %d illegal FENs, accepted %d legal ones "
          "including setup-only piece counts; engine_safe caught the "
          "%d-piece wall" % (len(cases), len(legal), 42))


def test_setup_game():
    # Replay of the checkmate-during-setup observed on the live analysis
    # board (docs/RULES.md): 1.@Qd1 @Qd8 2.@Pa2 @Ke6 3.@Qe1 is mate - the
    # block squares e2-e5 are outside Black's zone and the king cannot move.
    s = rules.SetupState()
    for pt, sq in [(chess.QUEEN, chess.D1), (chess.QUEEN, chess.D8),
                   (chess.PAWN, chess.A2), (chess.KING, chess.E6),
                   (chess.QUEEN, chess.E1)]:
        s.place(pt, sq)
    if not s.in_check(chess.BLACK):
        fail("Qe1 vs Ke6 not detected as check")
    if s.result != "1-0":
        fail("setup checkmate not detected (result %r)" % s.result)
    try:
        s.place(chess.PAWN, chess.A7)
        fail("placement accepted after setup checkmate")
    except ValueError:
        pass

    s = rules.SetupState()
    for bad_pt, bad_sq, label in [
            (chess.PAWN, chess.A1, "pawn placed on rank 1"),
            (chess.QUEEN, chess.E4, "piece placed on rank 4"),
            (chess.QUEEN, chess.E8, "piece placed in opponent zone")]:
        try:
            s.place(bad_pt, bad_sq)
            fail(label + " was accepted")
        except ValueError:
            pass

    # the king may not be placed onto an attacked square
    s = rules.SetupState()
    s.place(chess.QUEEN, chess.E1)
    try:
        s.place(chess.KING, chess.E7)
        fail("king placed onto an attacked square")
    except ValueError:
        pass

    # blockable setup check forces a block: Qe1 checks Ke7 down the open
    # e-file; the only block square inside Black's zone is e6, so every
    # legal reply must land there.
    s = rules.SetupState()
    s.place(chess.PAWN, chess.A2)
    s.place(chess.KING, chess.E7)
    s.place(chess.QUEEN, chess.E1)
    replies = s.legal_placements()
    if not replies:
        fail("blockable setup check reported as mate")
    if any(sq != chess.E6 for _, sq in replies):
        fail("reply to a setup check that does not block it")

    # a finished side is skipped: White spends all 39 points on queens and
    # pawns while Black buys one cheap pawn per turn, so White runs out
    # first and Black must keep placing alone.
    s = rules.SetupState()
    s.place(chess.KING, chess.E1)
    s.place(chess.KING, chess.E8)
    white = ([(chess.QUEEN, chess.square(f, 0)) for f in range(4)] +
             [(chess.PAWN, chess.square(f, 1)) for f in range(3)])
    black = [(chess.PAWN, chess.square(f, 6)) for f in range(len(white))]
    for w, b in zip(white, black):
        s.place(*w)
        if not s.done(chess.WHITE):
            s.place(*b)
    if s.points[chess.WHITE] != 0:
        fail("White did not spend its budget (%d left)" % s.points[chess.WHITE])
    if not s.done(chess.WHITE) or s.turn != chess.BLACK:
        fail("finished White was not skipped")
    if s.complete:
        fail("setup reported complete while Black still has points")
    s.place(chess.PAWN, chess.square(7, 6))
    if s.turn != chess.BLACK:
        fail("turn left Black while only Black had points")

    # many random games terminate in either a handoff or a setup mate
    mates = handoffs = 0
    for seed in range(60):
        rng = random.Random(seed)
        s = rules.SetupState()
        for _ in range(400):
            if s.result or s.complete:
                break
            s.place(*rng.choice(s.legal_placements()))
        else:
            fail("setup game did not terminate (seed %d)" % seed)
        if s.result:
            mates += 1
            continue
        handoffs += 1
        ok, why = rules.validate_fen(s.handoff_fen())
        if not ok:
            fail("handoff FEN invalid: %s" % why)
        if rules.BUDGET - s.points[chess.WHITE] > rules.BUDGET:
            fail("overspend")
    if not handoffs:
        fail("no random game reached a handoff")
    print("PASS: setup game flow (live-mate regression, forced blocks, "
          "illegal placements, finished-side skipping, %d random games: "
          "%d handoffs + %d setup mates)" % (handoffs + mates, handoffs, mates))


def test_engine(n, engine_path, rng):
    path = shutil.which(engine_path) or engine_path
    try:
        engine = chess.engine.SimpleEngine.popen_uci(path)
    except (OSError, chess.engine.EngineError) as e:
        fail("cannot start UCI engine %r: %s" % (engine_path, e))
    tried = skipped_invalid = skipped_over = 0
    try:
        while tried < n:
            fen = rules.setup_fen(random_army(rng), random_army(rng))
            ok, why = rules.validate_fen(fen)
            if not ok:
                # independent random armies can leave the non-mover in check,
                # unreachable in a real placement game; regenerate
                skipped_invalid += 1
                continue
            board = chess.Board(fen)
            if board.is_game_over():
                skipped_over += 1
                continue
            result = engine.play(board, chess.engine.Limit(depth=1))
            if result.move is None:
                fail("engine returned no move for %s" % fen)
            tried += 1
    finally:
        engine.quit()
    print("PASS: engine answered %d/%d setup FENs at depth 1 "
          "(%d invalid and %d already-decided positions regenerated)"
          % (tried, n, skipped_invalid, skipped_over))


def test_pool(rng):
    seen = set()
    for name, army in pool.ARCHETYPES.items():
        if rules.army_cost(army) > rules.BUDGET:
            fail("archetype %s costs %d" % (name, rules.army_cost(army)))
        filled = pool.complete(army, rng)
        ok, why = rules.validate_army(filled)
        if not ok:
            fail("archetype %s invalid after top-up: %s" % (name, why))
        seen.add(pool.army_key(filled))
    if len(seen) != len(pool.ARCHETYPES):
        fail("archetypes collapse to %d distinct armies" % len(seen))

    base = pool.seed_pool(rng)
    army = base[0]
    for _ in range(2000):
        army = pool.mutate(army, rng)  # raises on any illegal product
    sized = pool.seed_pool(rng, size=len(pool.ARCHETYPES) + 60)
    if len(sized) != len(pool.ARCHETYPES) + 60:
        fail("seed_pool returned %d armies" % len(sized))
    if len({pool.army_key(a) for a in sized}) != len(sized):
        fail("seed_pool produced duplicates")
    for a in sized:
        ok, why = rules.validate_army(a)
        if not ok:
            fail("pooled army invalid: %s" % why)
    print("PASS: %d archetypes legal and distinct, 2000 mutations legal, "
          "%d-army pool with no duplicates" % (len(pool.ARCHETYPES), len(sized)))


def test_arena_units():
    # jitter must vary by game and colour but repeat for identical inputs
    a = arena.game_nodes(20000, 0.15, 1, 2, 0, 0)
    if arena.game_nodes(20000, 0.15, 1, 2, 0, 0) != a:
        fail("game_nodes is not reproducible")
    varied = {arena.game_nodes(20000, 0.15, 1, 2, g, c)
              for g in range(8) for c in (0, 1)}
    if len(varied) < 12:
        fail("game_nodes gives only %d distinct counts over 16 games"
             % len(varied))
    if arena.game_nodes(20000, 0.0, 1, 2, 0, 0) != 20000:
        fail("jitter 0 should return the base node count")
    for v in varied:
        if not 17000 <= v <= 23000:
            fail("jittered node count %d outside the 15%% band" % v)

    cells = {(0, 0, 0): 0.5, (0, 1, 0): 1.0, (0, 1, 1): 0.0, (1, 0, 0): 0.25}
    m = arena.matrix_from_cells(cells, 2)
    if m[0][1] != 0.5 or m[1][0] != 0.25 or m[1][1] is not None:
        fail("matrix_from_cells averaged wrong: %r" % m)
    print("PASS: arena node jitter reproducible and banded, "
          "matrix averaging correct")


def test_arena_smoke(engine_path, tmpdir):
    """One real 2-setup campaign end to end, into a scratch path."""
    out = os.path.join(tmpdir, "selftest_arena_%d.json" % os.getpid())
    armies = pool.seed_pool(random.Random(1))[:2]
    with open(os.path.join(tmpdir, "selftest_pool.json"), "w") as f:
        json.dump([pool.to_json(a) for a in armies], f)
    cmd = [sys.executable, "arena.py", "--out", out,
           "--pool", os.path.join(tmpdir, "selftest_pool.json"),
           "--engine", engine_path, "--nodes", "600", "--pairs", "1",
           "--workers", "2"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        fail("arena.py exited %d:\n%s\n%s" % (r.returncode, r.stdout, r.stderr))
    with open(out) as f:
        state = json.load(f)
    if len(state["cells"]) != 4:
        fail("expected 4 cells, got %d" % len(state["cells"]))
    for key, score in state["cells"].items():
        if not 0.0 <= score <= 1.0:
            fail("score %r out of range in cell %s" % (score, key))
    # resume: a second run must play nothing and leave the cells untouched
    r2 = subprocess.run(cmd, capture_output=True, text=True)
    if "nothing to do" not in r2.stdout:
        fail("resume replayed finished cells:\n%s" % r2.stdout)
    with open(out) as f:
        if json.load(f)["cells"] != state["cells"]:
            fail("resume rewrote existing cells")
    print("PASS: arena end to end (4 cells played, resume replayed nothing)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engine", default="stockfish",
                    help="UCI engine binary name or path (default: stockfish)")
    ap.add_argument("--armies", type=int, default=10000)
    ap.add_argument("--fens", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--scratch", help="scratch dir for the arena smoke test "
                    "(default: the system temp dir; never a repo path)")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    test_armies(args.armies, rng)
    test_validate_fen()
    test_setup_game()
    test_pool(rng)
    test_arena_units()
    test_engine(args.fens, args.engine, rng)
    test_arena_smoke(args.engine, args.scratch or tempfile.gettempdir())
    print("OK: all selftests passed")


if __name__ == "__main__":
    main()
