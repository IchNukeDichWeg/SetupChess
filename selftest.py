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
import cengine
import pool
import play
import rules
import solve
import stats


# a node limit is checked between nodes, so a search overshoots by at most
# the work already in flight when it trips
MAX_OVERSHOOT = 4096


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


def test_bot_policy():
    """The two behaviours docs/BOT_MODEL.md actually measures."""
    # the +100*Y king clause, against every value read off their console
    for names, want in ((("a1", "h1"), 450.0), (("a2", "b1", "g1"), 400.0),
                        (("d1", "e1"), 300.0)):
        for n in names:
            got = play.bot_king_bonus(chess.parse_square(n))
            if got != want:
                fail("bot king bonus on %s is %.2f, their console said %.2f"
                     % (n, got, want))

    # king FIRST and in a corner, both colours. Observed live: the bot answered
    # our Ba1 with @Ka8, taking the corner the bishop had not denied.
    for color in (chess.WHITE, chess.BLACK):
        s = rules.SetupState()
        if color == chess.BLACK:
            s.place(chess.BISHOP, chess.A1)
        pt, sq = play.BotDrafter(color).choose(s)
        if pt != chess.KING:
            fail("bot did not place its king first as %s (played %s)"
                 % (chess.COLOR_NAMES[color], chess.piece_name(pt)))
        corners = ((chess.A1, chess.H1) if color == chess.WHITE
                   else (chess.A8, chess.H8))
        if sq not in corners:
            fail("bot king went to %s, not a corner" % chess.square_name(sq))

    # material is absent from their setup sum and the bias favours cheap
    # pieces, so the army must drift to pawns and knights. This is the oracle
    # that caught an unnormalised mobility term spending all 39 on queens.
    s = rules.SetupState()
    bot = play.BotDrafter(chess.WHITE)
    for _ in range(200):
        if s.result or s.done(chess.WHITE):
            break
        s.place(*bot.choose(s))
        s.turn = chess.WHITE          # solo drafting; no opponent to alternate
    counts = {}
    for sq, piece in s.board.piece_map().items():
        counts[piece.piece_type] = counts.get(piece.piece_type, 0) + 1
    cheap = counts.get(chess.PAWN, 0) + counts.get(chess.KNIGHT, 0)
    dear = sum(counts.get(pt, 0) for pt in
               (chess.BISHOP, chess.ROOK, chess.QUEEN))
    if dear or cheap < 20:
        fail("bot army is not pawns and knights: %r" % counts)
    # pool.BOT_WALL is a hardcoded copy of what the policy above drafts. If the
    # policy ever changes, this is what says so instead of the campaign quietly
    # breeding against a stale army.
    drafted = sorted((p.piece_type, sq)
                     for sq, p in s.board.piece_map().items())
    if sorted(pool.BOT_WALL) != drafted:
        fail("pool.BOT_WALL no longer matches what BotDrafter drafts")
    ok, why = rules.validate_army(pool.BOT_WALL)
    if not ok:
        fail("pool.BOT_WALL is not a legal army: %s" % why)
    if pool.BOT_WALL in pool.ARCHETYPES.values():
        fail("BOT_WALL leaked into ARCHETYPES, which would change the gate field")
    if len(pool.seed_pool(random.Random(2026))) != 12:
        fail("the archetype gate field is no longer 12 armies")

    # --seed-bot pins it, and the pin points at the wall
    import expand
    st = expand.new_state(2026, {}, seed_bot=True)
    if st["pinned"] != [12]:
        fail("seed_bot pinned %r, expected [12]" % st["pinned"])
    if pool.from_json(st["armies"][st["pinned"][0]]) != pool.BOT_WALL:
        fail("the pinned index does not hold BOT_WALL")
    if expand.new_state(2026, {})["pinned"]:
        fail("a campaign without --seed-bot pinned something")

    # the admission rule is CONJUNCTIVE. The previous version blended the
    # pinned score into the weighted average at 50%, which let a challenger
    # that merely beat the wall in on a 0.10 score against the support; the
    # pool went 13 -> 69 in three rounds and the fill cost quadrupled.
    idx = [0, 1, 2, 3]
    support = {0: 0.9, 1: 0.9, 2: 0.2, 3: 0.9}
    pin = {0: 0.9, 1: 0.2, 2: 0.9, 3: None}
    got, blocked = expand.admit(support, pin, idx, 0.5, [12])
    if got != [0]:
        fail("conjunctive admission let through %r, expected only [0]" % got)
    if blocked != 2:
        fail("blocked-by-pin counted %d, expected 2 (index 1 and 3)" % blocked)
    # with nothing pinned it is exactly the old support-only rule
    got, blocked = expand.admit(support, {}, idx, 0.5, [])
    if got != [0, 1, 3] or blocked:
        fail("admission changed when nothing is pinned: %r, %d" % (got, blocked))
    # a challenger that crushes the pin but loses the support stays out
    got, _ = expand.admit({9: 0.10}, {9: 0.99}, [9], 0.5, [12])
    if got:
        fail("a pin-beater with a 0.10 support score was admitted")

    # a pinned opponent may take equilibrium weight -- a wall that draws
    # everything is a valid strategy -- and must still be kept out of the
    # breeding parents and out of the gate mix, or the pool copies the bot and
    # the gate scores chess.com's own draft as ours
    w = expand.our_strategies({4: 0.45, 11: 0.05, 12: 0.5}, [12])
    if 12 in w:
        fail("the pinned opponent survived into our strategy set: %r" % w)
    if abs(sum(w.values()) - 1.0) > 1e-9:
        fail("our strategies were not renormalised: %r" % w)
    if abs(w[4] / w[11] - 9.0) > 1e-9:
        fail("renormalising changed the proportions: %r" % w)
    if expand.our_strategies({4: 0.9, 11: 0.1}, []) != {4: 0.9, 11: 0.1}:
        fail("our_strategies changed anything with nothing pinned")
    if expand.our_strategies({12: 1.0}, [12]) != {}:
        fail("an all-pinned equilibrium must come back empty, not renormalise")

    print("PASS: bot policy matches its 7 measured king-bonus values, opens "
          "with the king in a corner both colours, and drafts %d pawns and "
          "%d knights with nothing expensive, matching pool.BOT_WALL; "
          "--seed-bot pins it at index 12 and leaves the 12-army gate field "
          "alone" % (counts.get(chess.PAWN, 0), counts.get(chess.KNIGHT, 0)))


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

    # a finished side passes and the other keeps placing: White spends all 39
    # points on queens and pawns while Black buys one cheap pawn per turn, so
    # White runs out first and Black places alone from there. The board state
    # is what a "skip" would produce; the difference is only visible in the
    # turn parity once BOTH sides finish (see the live regression below).
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
        fail("the turn did not pass through a finished White")
    if s.complete:
        fail("setup reported complete while Black still has points")
    s.place(chess.PAWN, chess.square(7, 6))
    if s.turn != chess.BLACK:
        fail("turn left Black while only Black could still place")

    # A check the placer cannot be answered is mate ONLY while the placer
    # still has resources. If the placer finishes on the checking placement,
    # the setup ends and the checked side has the first move (see
    # rules.SetupState.mates) -- and this cuts both ways by colour, which the
    # old White-only form of the exception got wrong in both directions.
    def unanswerable(white_left, black_left):
        """Ka1 / Ke6 / Qe1: e2-e5 are outside Black's zone, so no block."""
        s = rules.SetupState()
        s.place(chess.KING, chess.A1)
        s.place(chess.KING, chess.E6)
        s.points[chess.WHITE] = white_left + rules.PIECE_COST[chess.QUEEN]
        s.points[chess.BLACK] = black_left
        s.place(chess.QUEEN, chess.E1)
        return s

    s = unanswerable(11, 39)
    if s.result != "1-0":
        fail("unanswerable setup check from a placer with points left is not "
             "mate (result %r)" % s.result)
    s = unanswerable(0, 39)
    if s.result is not None:
        fail("placer finished on the checking placement, so the checked side "
             "moves first, but the setup was scored %r" % s.result)
    if not s.complete or s.turn != chess.BLACK:
        fail("checked survivor does not have the move")
    board = chess.Board(s.handoff_fen())
    if not board.is_check() or not any(board.legal_moves):
        fail("checked survivor is not in check with an escape")

    # the colour-swapped case the old code scored backwards: Black checks a
    # finished White while still holding points, which IS mate.
    s = rules.SetupState()
    s.place(chess.KING, chess.E3)
    s.points[chess.WHITE] = 0
    s.place(chess.KING, chess.A8)
    s.points[chess.BLACK] = 9 + 5
    s.place(chess.QUEEN, chess.E8)
    if s.result != "0-1":
        fail("Black checking a finished White while still holding points "
             "should be mate (result %r)" % s.result)

    # THE SIDE TO MOVE AFTER SETUP IS NOT ALWAYS WHITE. Replay of a real game
    # driven against chess.com's own bot on 2026-08-05: White placed 16th and
    # last, Black had been passing since move 11, and Black opened the chess
    # phase with Qh3+. handoff_fen() used to hardcode White, which hands an
    # engine a position a tempo out of step.
    live = [("B", "a1"), ("K", "a8"), ("B", "c1"), ("P", "a6"),
            ("B", "d1"), ("B", "c6"), ("B", "g1"), ("P", "b6"),
            ("B", "e2"), ("Q", "c8"), ("B", "b3"), ("Q", "a7"),
            ("B", "c3"), ("Q", "b7"), ("B", "d2"), ("P", "g7"),
            ("B", "c2"), ("P", "h7"), ("K", "h2"), ("R", "b8"),
            ("B", "f3"), ("B", "g2"), ("B", "b2"), ("P", "f2"),
            ("P", "g3"), ("P", "a2")]
    s = rules.SetupState()
    for sym, name in live:
        s.place(chess.PIECE_SYMBOLS.index(sym.lower()), chess.parse_square(name))
    if not s.complete:
        fail("the live game did not complete")
    if s.turn != chess.BLACK:
        fail("side to move after the live setup is %s, the server had black"
             % chess.COLOR_NAMES[s.turn])
    want = "krq5/qq4pp/ppb5/8/8/1BB2BP1/PBBBBPBK/B1BB2B1 b - - 0 1"
    if s.handoff_fen() != want:
        fail("handoff FEN is\n  %s\nthe server had\n  %s"
             % (s.handoff_fen(), want))
    board = chess.Board(s.handoff_fen())
    if not any(board.san(m) == "Qh3+" for m in board.legal_moves):
        fail("Qh3+, the move the bot actually played, is not legal in our FEN")

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
    for _ in range(2000):
        pool.breed(base, rng)          # crossover and multi-step edits
    # crossover recombines rank bands, and a band covering all or none of one
    # parent legitimately reproduces a parent; breed() is what has to
    # guarantee a novel challenger, so that is what gets asserted.
    a, b = base[0], base[3]
    for _ in range(200):
        ok, why = rules.validate_army(pool.crossover(a, b, rng))
        if not ok:
            fail("crossover produced %s" % why)
    keys = {pool.army_key(x) for x in base}
    repeats = sum(1 for _ in range(300)
                  if pool.army_key(pool.breed(base, rng, crossover_rate=1.0))
                  in keys)
    if repeats > 15:
        fail("breed returned an existing army %d/300 times" % repeats)
    # a multi-step mutation must move further than a single one
    far = pool.mutate(base[0], rng, steps=3)
    if len(set(base[0]) - set(far)) < 2:
        fail("steps=3 changed fewer than two pieces")
    sized = pool.seed_pool(rng, size=len(pool.ARCHETYPES) + 60)
    if len(sized) != len(pool.ARCHETYPES) + 60:
        fail("seed_pool returned %d armies" % len(sized))
    if len({pool.army_key(a) for a in sized}) != len(sized):
        fail("seed_pool produced duplicates")
    for a in sized:
        ok, why = rules.validate_army(a)
        if not ok:
            fail("pooled army invalid: %s" % why)
    print("PASS: %d archetypes legal and distinct, 2000 mutations and 2000 "
          "bred armies legal, crossover recombines, %d-army pool with no "
          "duplicates" % (len(pool.ARCHETYPES), len(sized)))


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
           "--workers", "1"]
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


def test_solver():
    # the kickoff gate: rock-paper-scissors must come back (1/3, 1/3, 1/3)
    rps = [[0.5, 0.0, 1.0], [1.0, 0.5, 0.0], [0.0, 1.0, 0.5]]
    x, value = solve.nash(rps)
    if max(abs(v - 1 / 3) for v in x) > 1e-6:
        fail("RPS equilibrium is %r, want thirds" % (list(x),))
    if abs(value - 0.5) > 1e-9:
        fail("RPS value is %.6f, want 0.5" % value)
    if abs(solve.exploitability(rps, x)) > 1e-9:
        fail("RPS equilibrium reads as exploitable")

    # a dominant row must take the whole mix and be unexploitable
    dom = [[0.5, 1.0], [0.0, 0.5]]
    x, value = solve.nash(dom)
    if abs(x[0] - 1.0) > 1e-6 or abs(value - 0.5) > 1e-6:
        fail("dominant row not found: %r value %.4f" % (list(x), value))

    # a pure strategy in RPS is maximally exploitable: rock loses to paper
    if abs(solve.exploitability(rps, [1.0, 0.0, 0.0]) - 0.5) > 1e-9:
        fail("exploitability of pure rock is %.4f, want 0.5"
             % solve.exploitability(rps, [1.0, 0.0, 0.0]))

    # best_response picks the counter, not the mirror
    i, v = solve.best_response(rps, [1.0, 0.0, 0.0])
    if i != 1 or abs(v - 1.0) > 1e-9:
        fail("best response to pure rock is %d (%.3f), want paper" % (i, v))

    # antisymmetrisation must repair a noisy matrix back to value 0.5 and
    # leave an already-exact one untouched
    noisy = [[0.55, 0.80], [0.30, 0.48]]
    fixed = solve.antisymmetrize(noisy)
    for i in range(2):
        for j in range(2):
            if abs(fixed[i][j] + fixed[j][i] - 1.0) > 1e-12:
                fail("antisymmetrize left %d,%d asymmetric" % (i, j))
    if abs(solve.nash(fixed)[1] - 0.5) > 1e-9:
        fail("symmetric matrix did not solve to value 0.5")
    if abs(solve.nash(noisy)[1] - 0.5) < 1e-6:
        fail("the noisy matrix was already symmetric; test proves nothing")
    if solve.antisymmetrize(rps) != rps:
        fail("antisymmetrize changed an already-exact matrix")
    # a one-sided measurement is recovered from its mirror
    half = [[0.5, None], [0.25, 0.5]]
    if solve.antisymmetrize(half)[0][1] != 0.75:
        fail("missing cell not recovered from its mirror")
    print("PASS: RPS solves to thirds at value 0.500, dominant row found, "
          "exploitability and best response correct, antisymmetrisation "
          "restores value 0.5")


def test_solver_holes(tmpdir):
    """A setup whose row is mostly unmeasured must be dropped, not imputed."""
    path = os.path.join(tmpdir, "selftest_holes_%d.json" % os.getpid())
    cells = {}
    for i in range(3):
        for j in range(3):
            # setup 2 is unplayable against everything except itself
            cells["%d,%d,0" % (i, j)] = None if 2 in (i, j) and i != j else 0.5
    with open(path, "w") as f:
        json.dump({"meta": {"n": 3}, "cells": cells}, f)
    m, keep, rep = solve.load_matrix(path, max_holes=1)
    if keep != [0, 1]:
        fail("hole filter kept %r, want [0, 1]" % keep)
    if rep["dropped"] != [(2, 2)]:
        fail("hole filter reported %r" % rep["dropped"])
    if rep["filled"]:
        fail("filled %d cells that were all measured" % rep["filled"])
    m2, keep2, rep2 = solve.load_matrix(path, max_holes=2)
    if keep2 != [0, 1, 2] or rep2["filled"] != 4:
        fail("loose filter kept %r filling %d" % (keep2, rep2["filled"]))
    print("PASS: unmeasured rows dropped at the threshold, imputed cells counted")


def test_stats():
    if abs(stats.elo(0.5)) > 1e-9:
        fail("elo(0.5) is %r, want 0" % stats.elo(0.5))
    for e in (-400.0, -50.0, 0.0, 7.5, 300.0):
        back = stats.elo(stats.score_of(e))
        if abs(back - e) > 1e-6:
            fail("elo/score_of round trip broke at %g (got %g)" % (e, back))
    if stats.elo(0.75) <= 0 or stats.elo(0.25) >= 0:
        fail("elo sign is inverted")

    # a coin flip must not clear either SPRT bound, and its CI must span 0
    rng = random.Random(5)
    coin = [rng.choice([0.0, 0.5, 1.0]) for _ in range(400)]
    e, lo, hi, st = stats.elo_with_ci(coin)
    if not lo < 0 < hi:
        fail("coin-flip CI [%g, %g] excludes 0" % (lo, hi))
    if stats.sprt_verdict(stats.sprt_llr(coin)) != "CONTINUE":
        fail("coin flip reached an SPRT bound")

    # a decisive edge must accept H1 and report a positive lower bound
    strong = [1.0] * 150 + [0.5] * 40 + [0.0] * 10
    e, lo, hi, st = stats.elo_with_ci(strong)
    if lo <= 0:
        fail("a 74%% scorer has lower Elo bound %g" % lo)
    if stats.sprt_verdict(stats.sprt_llr(strong)) != "ACCEPT H1":
        fail("decisive edge did not accept H1 (LLR %.3f)"
             % stats.sprt_llr(strong))
    # and a decisive loss must accept H0
    if stats.sprt_verdict(stats.sprt_llr([0.0] * 150 + [0.5] * 50)) != "ACCEPT H0":
        fail("decisive loss did not accept H0")
    if "+/-" not in stats.report(coin):
        fail("report() omitted the error margin")
    print("PASS: elo round trip, coin flip continues with a CI spanning zero, "
          "decisive edge accepts H1")


def test_expand_units(tmpdir):
    import expand
    path = os.path.join(tmpdir, "selftest_expand_%d.json" % os.getpid())
    meta = {"m": 1}
    state = expand.new_state(2026, meta)
    n = len(state["armies"])
    if n != len(pool.ARCHETYPES):
        fail("new_state seeded %d armies" % n)

    # cell_tasks must cover both directions and never re-queue a done cell
    armies = [pool.from_json(a) for a in state["armies"]]
    tasks = expand.cell_tasks(armies, 1, {}, [0], [1], 100, 0.0)
    if {(t[0], t[1]) for t in tasks} != {(0, 1), (1, 0)}:
        fail("cell_tasks missed a direction: %r" % [(t[0], t[1]) for t in tasks])
    tasks = expand.cell_tasks(armies, 1, {(0, 1, 0): 0.5}, [0], [1], 100, 0.0)
    if {(t[0], t[1]) for t in tasks} != {(1, 0)}:
        fail("cell_tasks re-queued a finished cell")
    # overlapping index lists must not queue a cell twice: the (i, j) and
    # (j, i) passes meet in the middle and used to double the seeding bill
    idx = list(range(len(armies)))
    tasks = expand.cell_tasks(armies, 2, {}, idx, idx, 100, 0.0)
    distinct = {(t[0], t[1], t[2]) for t in tasks}
    if len(tasks) != len(distinct):
        fail("cell_tasks queued %d tasks for %d cells"
             % (len(tasks), len(distinct)))
    if len(distinct) != len(armies) ** 2 * 2:
        fail("cell_tasks covered %d of %d cells"
             % (len(distinct), len(armies) ** 2 * 2))

    # screen_scores must weight by the mix and ignore unmeasured opponents.
    # It reads a plain cells dict, not the state, so a challenger that fails
    # the screen never touches the pool matrix.
    scratch = {(2, 0, 0): 1.0, (0, 2, 0): 0.0, (2, 1, 0): 0.0, (1, 2, 0): 1.0}
    got = expand.screen_scores(scratch, n, [2], {0: 0.75, 1: 0.25})
    if abs(got[2] - 0.75) > 1e-9:
        fail("screen_scores gave %r, want 0.75" % got[2])
    got = expand.screen_scores(scratch, n, [3], {0: 1.0})
    if got[3] is not None:
        fail("screen_scores invented a score for an unmeasured challenger")
    if state["cells"]:
        fail("screening leaked cells into the pool state")

    # round trip through disk
    expand.save_state(path, state)
    back = expand.load_state(path, 2026, meta)
    if back["armies"] != state["armies"] or back["cells"] != state["cells"]:
        fail("state did not survive a save/load round trip")
    print("PASS: expand state round trip, cell_tasks covers both directions "
          "and skips done cells, screen_scores weights by the mix")


def test_expand_smoke(engine_path, tmpdir):
    """Two real rounds end to end, into a scratch path."""
    path = os.path.join(tmpdir, "selftest_expand_run_%d.json" % os.getpid())
    cmd = [sys.executable, "expand.py", "--state", path,
           "--engine", engine_path, "--rounds", "2", "--challengers", "3",
           "--screen-pairs", "1", "--pairs", "1", "--nodes", "400",
           "--max-pool", "14", "--workers", "1", "--final-games", "4"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        fail("expand.py exited %d:\n%s\n%s" % (r.returncode, r.stdout, r.stderr))
    if "gate match" not in r.stdout or "SPRT" not in r.stdout:
        fail("expand.py produced no gate report:\n%s" % r.stdout)
    with open(path) as f:
        state = json.load(f)
    if not state["rounds"]:
        fail("expand.py recorded no rounds")
    for a in state["armies"]:
        ok, why = rules.validate_army(pool.from_json(a))
        if not ok:
            fail("expansion produced an illegal army: %s" % why)
    if len(state["armies"]) > 14:
        fail("pool grew past --max-pool: %d" % len(state["armies"]))
    # killed challengers must not sit in the pool as unplayed columns: every
    # surviving setup needs a row the solver can actually use
    admitted = sum(r.get("admitted", 0) for r in state["rounds"])
    if len(state["armies"]) > len(pool.ARCHETYPES) + admitted:
        fail("pool holds %d setups but only %d were admitted on top of %d "
             "archetypes" % (len(state["armies"]), admitted, len(pool.ARCHETYPES)))
    m, keep, rep = solve.load_matrix(path, max_holes=2)
    if not keep:
        fail("no setup survived the hole filter after expansion")
    # resume must not redo finished rounds
    r2 = subprocess.run(cmd, capture_output=True, text=True)
    if r2.returncode != 0:
        fail("resume exited %d:\n%s" % (r2.returncode, r2.stderr))
    with open(path) as f:
        if len(json.load(f)["rounds"]) < len(state["rounds"]):
            fail("resume lost rounds")
    print("PASS: expand end to end (%d rounds, %d setups, gate match reported, "
          "resume kept its rounds)" % (len(state["rounds"]), len(state["armies"])))


def test_drafter():
    champ = play.load_army("campaigns/champion_v2.json")
    ok, why = rules.validate_army(champ)
    if not ok:
        fail("frozen champion is illegal: %s" % why)

    # unobstructed, the drafter must realise its target army exactly -- the
    # opponent places only in their own zone, so nothing can block us
    for color in (chess.WHITE, chess.BLACK):
        us = play.Drafter(champ, color)
        them = play.Drafter(play.load_army("classic"), not color)
        state = play.draft(*( (us, them) if color == chess.WHITE
                              else (them, us) ))
        if state.result:
            fail("draft ended in a setup result against a passive opponent")
        want = {(pt, sq) for pt, sq in us.target}
        got = {(p.piece_type, sq)
               for sq, p in state.board.piece_map().items() if p.color == color}
        if got != want:
            fail("drafter as %s built %d/%d target pieces"
                 % (chess.COLOR_NAMES[color], len(got & want), len(want)))
        ok, why = rules.validate_fen(state.handoff_fen())
        if not ok:
            fail("drafted handoff FEN invalid: %s" % why)

    # tactics beat the plan: with mate available the drafter must take it.
    # Black king on e6, e-file open, White to place -> @Qe1 is mate.
    state = rules.SetupState()
    for pt, sq in [(chess.QUEEN, chess.D1), (chess.QUEEN, chess.D8),
                   (chess.PAWN, chess.A2), (chess.KING, chess.E6)]:
        state.place(pt, sq)
    d = play.Drafter(champ, chess.WHITE)
    pt, sq = d.choose(state)
    state.place(pt, sq)
    if state.result != "1-0":
        fail("drafter missed the setup mate, played %s%s"
             % (chess.piece_symbol(pt).upper(), chess.square_name(sq)))

    # forced blocks: in check, every choice must answer the check
    state = rules.SetupState()
    state.place(chess.PAWN, chess.A2)
    state.place(chess.KING, chess.E7)
    state.place(chess.QUEEN, chess.E1)
    d = play.Drafter(champ, chess.BLACK)
    pt, sq = d.choose(state)
    if sq != chess.E6:
        fail("drafter did not block a setup check (played %s)"
             % chess.square_name(sq))
    # king hunt: an opponent that holds its king back while buying few, big
    # pieces can be locked out entirely -- ending setup with no king loses
    # outright. Pins the ablation so a change to either feature shows up.
    S = chess.parse_square

    def _army(spec):
        return [(chess.PIECE_SYMBOLS.index(tok[0].lower()), S(tok[1:]))
                for tok in spec.split()]

    styles = {
        "dense": "Ra8 Nb8 Bc8 Qd8 Bf8 Ng8 Rh8 Pa7 Pb7 Pc7 Pd7 Pe7 Pf7 Pg7 Ph7",
        "queen_spam": "Qa8 Qb8 Qc8 Qd8 Ng8",
        "rank3": "Qd6 Qe6 Rc6 Rf6 Bb6 Bg6 Pa6 Ph6 Pd7",
        "heavy": "Qd8 Qe8 Ra8 Rh8 Bc8 Pf7 Pg7 Ph7",
    }

    def run(plan, **kw):
        drafter = play.Drafter(champ, chess.WHITE, **kw)
        st, bi = rules.SetupState(), 0
        for _ in range(200):
            if st.result or st.complete:
                break
            if st.turn == chess.WHITE:
                mv = drafter.choose(st)
            else:
                legal = set(st.legal_placements())
                mv = next((x for x in plan[bi:] if x in legal), None)
                if mv is None:
                    kings = [x for x in legal if x[0] == chess.KING]
                    if not kings:
                        return "lockout"
                    mv = kings[0]
                else:
                    bi = plan.index(mv) + 1
            st.place(*mv)
        return st.result or "survived"

    got = {k: run(_army(v)) for k, v in styles.items()}
    want = {"dense": "survived", "queen_spam": "1-0",
            "rank3": "lockout", "heavy": "lockout"}
    if got != want:
        fail("king hunt changed outcomes: %r, want %r" % (got, want))
    # with the hunt disabled, heavy is a mate rather than a lockout: the hunt
    # is what converts it, and neither loses a win the baseline had
    if run(_army(styles["heavy"]), hunt_when=-1) != "1-0":
        fail("baseline no longer mates the heavy style")
    print("PASS: drafter realises its target for both colours, takes a setup "
          "mate over the plan, blocks a forced check, and locks out %d of %d "
          "king-last styles" % (sum(1 for v in got.values() if v == "lockout"),
                                len(got)))


def test_play_smoke(engine_path, tmpdir):
    out = os.path.join(tmpdir, "selftest_play_%d.json" % os.getpid())
    r = subprocess.run([sys.executable, "play.py", "--target",
                        "campaigns/champion_v2.json", "--opponent", "classic",
                        "--engine", engine_path, "--nodes", "400",
                        "--out", out], capture_output=True, text=True)
    if r.returncode != 0:
        fail("play.py exited %d:\n%s\n%s" % (r.returncode, r.stdout, r.stderr))
    with open(out) as f:
        games = json.load(f)["games"]
    if len(games) != 2:
        fail("expected one game each way, got %d" % len(games))
    for g in games:
        if g["result"] not in ("1-0", "0-1", "1/2-1/2"):
            fail("game ended %r" % g["result"])
        if g["fen"]:
            ok, why = rules.validate_fen(g["fen"])
            if not ok:
                fail("played-out FEN invalid: %s" % why)
    print("PASS: play.py end to end, one game each colour, both reached a result")


def _pyperft(board, depth):
    if depth == 0:
        return 1
    if depth == 1:
        return board.legal_moves.count()
    n = 0
    for m in board.legal_moves:
        board.push(m)
        n += _pyperft(board, depth - 1)
        board.pop()
    return n


def test_c_core(rng):
    """The C generator must agree with python-chess node for node.

    Published perft numbers assume castling, which the variant does not have,
    so python-chess with the rights stripped is the reference. The dense
    cases are the point: they are what Stockfish cannot take.
    """
    try:
        start = chess.Board()
        start.set_castling_fen("-")
        if cengine.perft(start, 4) != 197281:
            fail("startpos perft(4) is %d, want 197281"
                 % cengine.perft(start, 4))
    except OSError as e:
        fail("%s" % e)

    armies = pool.seed_pool(rng, size=24)
    champ = play.load_army("campaigns/champion_v2.json")
    wall = pool.complete(pool.ARCHETYPES["pawn_wall"], random.Random(0))
    cases = [("champion mirror", rules.setup_fen(champ, champ)),
             ("42-piece wall mirror", rules.setup_fen(wall, wall))]
    for k in range(6):
        cases.append(("random %d" % k,
                      rules.setup_fen(rng.choice(armies), rng.choice(armies))))

    dense = 0
    for label, fen in cases:
        board = chess.Board(fen)
        n = len(board.piece_map())
        dense = max(dense, n)
        depth = 2 if n > 34 else 3
        got, want = cengine.perft(board, depth), _pyperft(board, depth)
        if got != want:
            fail("perft mismatch on %s at depth %d: C %d, python-chess %d\n%s"
                 % (label, depth, got, want, fen))
        # the move lists themselves must match, not just their counts
        mine = sorted(m.uci() for m in cengine.legal_moves(board))
        theirs = sorted(m.uci() for m in board.legal_moves)
        if mine != theirs:
            fail("move list differs on %s: only C %r, only py %r"
                 % (label, set(mine) - set(theirs), set(theirs) - set(mine)))

    # en passant and promotion, the two places a generator usually breaks
    for label, fen, depth in [
            ("en passant available", "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", 4),
            ("promotion race", "4k3/1P6/8/8/8/8/6p1/4K3 w - - 0 1", 4),
            ("pawn on rank 3, no double step",
             "4k3/8/8/8/8/4P3/8/4K3 w - - 0 1", 4)]:
        board = chess.Board(fen)
        got, want = cengine.perft(board, depth), _pyperft(board, depth)
        if got != want:
            fail("perft mismatch on %s: C %d, python-chess %d" % (label, got, want))
    print("PASS: C core matches python-chess on %d positions up to %d pieces, "
          "plus en passant, promotion and the rank-3 pawn" % (len(cases), dense))


def test_match_smoke(engine_path, tmpdir):
    """The paired A/B harness runs and reports a difference with its CI."""
    out = os.path.join(tmpdir, "selftest_match_%d.json" % os.getpid())
    r = subprocess.run([sys.executable, "match.py",
                        "--target", "campaigns/champion_v2.json",
                        "--pool", "campaigns/expand_v2.json", "--out", out,
                        "--games", "4", "--nodes", "300", "--workers", "1",
                        "--engine", engine_path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        fail("match.py exited %d:\n%s\n%s" % (r.returncode, r.stdout, r.stderr))
    for needed in ("paired difference", "SPRT", "Elo:"):
        if needed not in r.stdout:
            fail("match.py report is missing %r:\n%s" % (needed, r.stdout))
    with open(out) as f:
        d = json.load(f)
    by_index = d["by_index"]
    if not by_index:
        fail("match.py recorded no games")
    for k, pair in by_index.items():
        if len(pair) != 2:
            fail("game %s is not a pair: %r" % (k, pair))
        for s_ in pair:
            if s_ not in (0.0, 0.5, 1.0):
                fail("score %r is not a game result" % s_)

    # resume: asking for the same games again must replay nothing, and the
    # recorded results must be identical
    r2 = subprocess.run([sys.executable, "match.py",
                         "--target", "campaigns/champion_v2.json",
                         "--pool", "campaigns/expand_v2.json", "--out", out,
                         "--games", "4", "--nodes", "300", "--workers", "1",
                         "--engine", engine_path],
                        capture_output=True, text=True)
    if r2.returncode != 0:
        fail("match resume exited %d:\n%s" % (r2.returncode, r2.stderr))
    if "resuming: %d games" % len(by_index) not in r2.stdout:
        fail("match.py did not resume:\n%s" % r2.stdout)
    with open(out) as f:
        if json.load(f)["by_index"] != by_index:
            fail("resume changed recorded games")
    print("PASS: match.py paired A/B harness runs, pairs are well formed, "
          "and a resume replays nothing")


# The bench is a signature, not a benchmark: it must change when the search
# or the eval changes and must not otherwise. Update it in the SAME commit as
# any deliberate change, and treat a surprise move as a bug.
BENCH_DEPTH = 5
BENCH_NODES = 404905


def test_search(rng):
    """Eval symmetry, mate finding, the node budget, and the bench oracle."""
    # a mirrored position must evaluate identically from the new side to
    # move: this catches almost every sign and rank-flip bug in one line
    armies = pool.seed_pool(rng, size=20)
    checked = 0
    for _ in range(200):
        fen = rules.setup_fen(rng.choice(armies), rng.choice(armies))
        if not rules.validate_fen(fen)[0]:
            continue
        board = chess.Board(fen)
        checked += 1
        if cengine.evaluate(board) != cengine.evaluate(board.mirror()):
            fail("eval is not mirror-symmetric on %s" % fen)
    if checked < 50:
        fail("only %d positions were eval-checked" % checked)
    start = chess.Board()
    start.set_castling_fen("-")
    if cengine.evaluate(start) != 0:
        fail("startpos evaluates to %d, want 0" % cengine.evaluate(start))

    # mates, both directions, and the score sign
    mates = [("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", "a1a8"),
             ("r5k1/8/8/8/8/8/5PPP/6K1 b - - 0 1", "a8a1")]
    for fen, want in mates:
        mv, score, _ = cengine.search(chess.Board(fen), depth=4)
        if mv.uci() != want:
            fail("mate in one on %s played %s, want %s" % (fen, mv, want))
        if score < 29000:
            fail("mate scored %d, expected a mate score" % score)

    # the node budget must be respected, and a move must always come back
    for limit in (100, 1000, 20000):
        mv, _, used = cengine.search(start, nodes=limit)
        if used > limit + MAX_OVERSHOOT:
            fail("node limit %d overshot to %d" % (limit, used))
        if mv is None or mv not in start.legal_moves:
            fail("search returned %r at limit %d" % (mv, limit))

    # every move the search returns must be legal, on dense boards too
    for _ in range(40):
        fen = rules.setup_fen(rng.choice(armies), rng.choice(armies))
        if not rules.validate_fen(fen)[0]:
            continue
        board = chess.Board(fen)
        mv, _, _ = cengine.search(board, nodes=2000)
        if mv is not None and mv not in board.legal_moves:
            fail("illegal move %s on %s" % (mv, fen))

    # the 42-piece wall: the whole reason this core exists
    wall = chess.Board(
        "rn1qk2r/pppppppp/pppppppp/8/8/PPPPPPPP/PPPPPPPP/RN1QK2R w - - 0 1")
    mv, _, _ = cengine.search(wall, nodes=20000)
    if mv is None or mv not in wall.legal_moves:
        fail("search failed on the 42-piece position Stockfish crashes on")

    # bench signature
    champ = play.load_army("campaigns/champion_v2.json")
    bench_pos = chess.Board(rules.setup_fen(champ, champ))
    runs = {cengine.search(bench_pos, depth=BENCH_DEPTH)[2] for _ in range(2)}
    if len(runs) != 1:
        fail("bench is not reproducible: %r" % runs)
    got = runs.pop()
    if got != BENCH_NODES:
        fail("bench is %d, expected %d -- if the search or eval changed on "
             "purpose, update BENCH_NODES in this commit" % (got, BENCH_NODES))
    print("PASS: eval mirror-symmetric over %d positions, mates found, node "
          "budget honoured, 42-piece position searched, bench %d" %
          (checked, got))


def test_uci(tmpdir):
    """The core answers UCI and plays a legal game through the front end."""
    eng = chess.engine.SimpleEngine.popen_uci("./cuci.py")
    try:
        board = chess.Board()
        board.set_castling_fen("-")
        for _ in range(12):
            if board.is_game_over(claim_draw=True):
                break
            mv = eng.play(board, chess.engine.Limit(nodes=2000)).move
            if mv is None or mv not in board.legal_moves:
                fail("cuci.py returned %r, illegal in %s" % (mv, board.fen()))
            board.push(mv)
        # and on the position Stockfish cannot take
        wall = chess.Board(
            "rn1qk2r/pppppppp/pppppppp/8/8/PPPPPPPP/PPPPPPPP/RN1QK2R w - - 0 1")
        mv = eng.play(wall, chess.engine.Limit(nodes=5000)).move
        if mv is None or mv not in wall.legal_moves:
            fail("cuci.py failed on the 42-piece position")
    finally:
        eng.quit()
    print("PASS: cuci.py speaks UCI, plays 12 legal plies and the 42-piece "
          "position")


def test_duel_smoke(engine_path, tmpdir):
    """Engine-vs-engine harness runs, pairs are colour-balanced, resume works."""
    out = os.path.join(tmpdir, "selftest_duel_%d.json" % os.getpid())
    cmd = [sys.executable, "duel.py", "--engine-a", "./cuci.py",
           "--engine-b", engine_path, "--out", out, "--games", "2",
           "--nodes", "400", "--workers", "1"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        fail("duel.py exited %d:\n%s\n%s" % (r.returncode, r.stdout, r.stderr))
    if "Elo:" not in r.stdout:
        fail("duel.py reported no Elo:\n%s" % r.stdout)
    if "SPRT" in r.stdout:
        fail("duel.py printed an SPRT verdict, which is not meaningful for a "
             "magnitude comparison against a stronger reference")
    with open(out) as f:
        d = json.load(f)
    for k, pair in d["by_index"].items():
        if len(pair) != 2:
            fail("pair %s is not two games: %r" % (k, pair))
        for s_ in pair:
            if s_ not in (0.0, 0.5, 1.0):
                fail("score %r is not a game result" % s_)
    r2 = subprocess.run(cmd, capture_output=True, text=True)
    if r2.returncode != 0 or "resuming" not in r2.stdout:
        fail("duel.py did not resume:\n%s" % r2.stdout)
    print("PASS: duel.py engine-vs-engine harness runs and resumes")


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
    test_bot_policy()
    test_setup_game()
    test_pool(rng)
    test_arena_units()
    test_solver()
    test_solver_holes(args.scratch or tempfile.gettempdir())
    test_stats()
    test_expand_units(args.scratch or tempfile.gettempdir())
    test_drafter()
    test_c_core(rng)
    test_search(rng)
    test_uci(args.scratch or tempfile.gettempdir())
    test_engine(args.fens, args.engine, rng)
    test_arena_smoke(args.engine, args.scratch or tempfile.gettempdir())
    test_expand_smoke(args.engine, args.scratch or tempfile.gettempdir())
    test_play_smoke(args.engine, args.scratch or tempfile.gettempdir())
    test_match_smoke(args.engine, args.scratch or tempfile.gettempdir())
    test_duel_smoke(args.engine, args.scratch or tempfile.gettempdir())
    print("OK: all selftests passed")


if __name__ == "__main__":
    main()
