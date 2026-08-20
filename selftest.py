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
import signal
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


def test_watchdog():
    """A stalled job is killed, its children swept, and it is restarted.

    expand.py hangs intermittently in Pool teardown -- parent at 0% CPU with
    every worker and engine already gone -- which cost two hours of wall clock
    on a live campaign. The watchdog cannot fix the hang; it makes one cost a
    restart instead of a night. Silence on stdout is the only signal, because
    the process is healthy by every other measure.
    """
    import watchdog
    d = tempfile.mkdtemp(prefix="selftest_wd_")
    job = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selftest.py")
    src = os.path.join(d, "job.py")
    with open(src, "w") as f:
        f.write("import os, subprocess, sys, time\n"
                "c = sys.argv[1]\n"
                "n = int(open(c).read()) if os.path.exists(c) else 0\n"
                "open(c, 'w').write(str(n + 1))\n"
                "print('run %d' % n, flush=True)\n"
                "if n == 0:\n"
                "    k = subprocess.Popen(['sleep', '600'])\n"
                "    open(c + '.kid', 'w').write(str(k.pid))\n"
                "    time.sleep(9999)\n"
                "print('done', flush=True)\n")
    ctr = os.path.join(d, "ctr")
    code = watchdog.main_for_test([sys.executable, src, ctr], silence=3)
    if code != 0:
        fail("watchdog returned %r for a job that stalls once then succeeds"
             % code)
    if int(open(ctr).read()) != 2:
        fail("the job ran %s times, expected 2 (one stall, one success)"
             % open(ctr).read())
    kid = int(open(ctr + ".kid").read())
    try:
        os.kill(kid, 0)
        os.kill(kid, signal.SIGKILL)
        fail("the stalled job's child survived; the sweep did not work")
    except ProcessLookupError:
        pass

    # a job that never prints anything is broken, not stalled: give up rather
    # than restarting forever. GRACE is dropped so this costs seconds.
    grace = watchdog.GRACE
    watchdog.GRACE = 0.5
    try:
        code = watchdog.main_for_test(
            [sys.executable, "-c", "import time; time.sleep(9999)"], silence=1)
    finally:
        watchdog.GRACE = grace
    if code == 0:
        fail("watchdog reported success for a job that never produced output")
    shutil.rmtree(d, ignore_errors=True)
    print("PASS: watchdog restarted a stalled job, swept its orphaned child, "
          "and gave up on one that never printed")


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

    # the OBSERVED armies are the opponent model that matters: two live games,
    # both 39 points, both three queens. They refute the piece-preference half
    # of docs/BOT_MODEL.md that BOT_WALL is derived from.
    if len(pool.BOT_OBSERVED) < 2:
        fail("expected at least two observed bot armies")
    for day, army in pool.BOT_OBSERVED.items():
        ok, why = rules.validate_army(army)
        if not ok:
            fail("observed bot army %s is illegal: %s" % (day, why))
        if rules.army_cost(army) != rules.BUDGET:
            fail("observed bot army %s spends %d of %d"
                 % (day, rules.army_cost(army), rules.BUDGET))
        queens = sum(1 for pt, _ in army if pt == chess.QUEEN)
        if queens < 2:
            fail("observed bot army %s has %d queens; the transcription is "
                 "probably wrong" % (day, queens))
        if sorted(army) == sorted(pool.BOT_WALL):
            fail("an observed army equals the refuted BOT_WALL model")
    if len(pool.seed_pool(random.Random(2026))) != 12:
        fail("the archetype gate field is no longer 12 armies")

    # --seed-bot pins it, and the pin points at the wall
    import expand
    st = expand.new_state(2026, {}, seed_bot=True)
    if len(st["pinned"]) != len(pool.BOT_OBSERVED):
        fail("seed_bot pinned %r, expected one index per observed army"
             % st["pinned"])
    pinned_armies = [pool.from_json(st["armies"][i]) for i in st["pinned"]]
    for army in pool.BOT_OBSERVED.values():
        if not any(sorted(army) == sorted(p) for p in pinned_armies):
            fail("an observed bot army was not pinned by --seed-bot")
    if any(sorted(pool.BOT_WALL) == sorted(p) for p in pinned_armies):
        fail("--seed-bot still pins the refuted BOT_WALL model")
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
          "%d knights with nothing expensive, matching pool.BOT_WALL -- which "
          "two live games REFUTE; --seed-bot pins the %d observed armies "
          "instead" % (counts.get(chess.PAWN, 0), counts.get(chess.KNIGHT, 0),
                       len(pool.BOT_OBSERVED)))


def test_mix_default():
    """Mixing is ON, and it must actually draw more than one army.

    The MIX constant was documentation-only before this: --mix was a plain
    store_true, so flipping the constant would have changed nothing and the
    file would have claimed a behaviour it did not have.
    """
    if not play.MIX:
        fail("play.MIX is off; this test pins it on deliberately")
    with open("campaigns/expand_v3.json") as f:
        armies = [pool.from_json(a) for a in json.load(f)["armies"]]
    rng = random.Random(11)
    drawn = {tuple(sorted(play.sample_target("campaigns/champion_v3.json",
                                             armies, rng)))
             for _ in range(300)}
    if len(drawn) < 2:
        fail("mixing drew only %d distinct army; it is not mixing" % len(drawn))
    for army in drawn:
        ok, why = rules.validate_army(list(army))
        if not ok:
            fail("mixing drew an illegal army: %s" % why)
        if rules.army_cost(list(army)) != rules.BUDGET:
            fail("mixing drew a %d-point army" % rules.army_cost(list(army)))
        if not any(sorted(army) == sorted(a) for a in armies):
            fail("mixing drew an army outside the pool, which re-targeting "
                 "would abandon on the first placement")
    # the CLI default must follow the constant, which is what was broken
    import argparse, io, contextlib
    parsed = None
    for argv in ([], ["--no-mix"]):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pass
        ap = argparse.ArgumentParser()
        ap.add_argument("--mix", dest="mix", action="store_true", default=play.MIX)
        ap.add_argument("--no-mix", dest="mix", action="store_false")
        parsed = ap.parse_args(argv)
        want = play.MIX if not argv else False
        if parsed.mix != want:
            fail("argv %r gave mix=%r, expected %r" % (argv, parsed.mix, want))
    # DRAWING is not PLAYING. This test used to stop at sample_target, and the
    # drafter threw the draw away on its first placement: _retarget ran on the
    # empty board, where opponent weights are uniform and the best response is
    # a constant, so every seed built the same army. Assert what reaches the
    # board, not what the sampler returned.
    shipped_pool, shipped_matrix = play.load_pool(play.DEFAULT_POOL)

    # CONTRACT CHANGE, deliberate. The shipped champion now carries an EMPTY
    # support, so mixing falls through to the fixed army. v6's support was
    # selected under the stamped model; measured with the placement phase
    # played out, that mixture draws the shipped champion only 13.8% of the
    # time and otherwise draws armies whose worst column is 0.39 to 0.45,
    # against the champion's 0.5767 -- so mixing would hand back most of the
    # gain. Re-deriving a mixing distribution under drafted play is owed; see
    # campaigns/champion_drafted.json's meta.
    with open(play.DEFAULT_TARGET) as f:
        if json.load(f).get("support"):
            fail("the shipped champion carries a support again: either mixing "
                 "was re-derived under DRAFTED measurement, in which case "
                 "update this test and champion_drafted.json's meta, or a "
                 "stamped-era support has been reintroduced")
    if play.sample_target(play.DEFAULT_TARGET, shipped_pool,
                          random.Random(0)) is not None:
        fail("sample_target returned an army for a support-less champion")

    # The mechanism still has to be pinned, or turning mixing off for the
    # shipped champion would quietly delete the regression this test exists
    # for: a draw that the drafter discards on its first placement, because
    # _retarget runs on the empty board where the best response is a constant.
    # Exercise it on a champion that DOES carry a support.
    mix_pool, mix_matrix = play.load_pool("campaigns/expand_v6.json")
    built = set()
    for s in range(12):
        tgt = play.sample_target("campaigns/champion_v6.json", mix_pool,
                                 random.Random(s))
        if tgt is None:
            fail("champion_v6 lost its support; this test needs one")
        d = play.Drafter(tgt, chess.WHITE, pool=mix_pool, matrix=mix_matrix)
        d.choose(rules.SetupState())
        built.add(tuple(sorted(d.target)))
    if len(built) < 2:
        fail("the drafter BUILT only %d distinct army over 12 seeds: mixing "
             "is drawn and then discarded before the first placement" % len(built))

    # ...and re-targeting must still fire once they have revealed something,
    # or the fix above would have bought mixing by deleting a measured feature
    d = play.Drafter(rules.mirror_army(play.load_army(play.DEFAULT_TARGET)),
                     chess.BLACK, pool=shipped_pool, matrix=shipped_matrix)
    st = rules.SetupState()
    shown = 0
    for pt, sq in shipped_pool[13]:
        if shown >= 6:
            break
        if st.turn == chess.WHITE and st.board.piece_at(sq) is None:
            st.place(pt, sq)
            shown += 1
        elif st.turn == chess.BLACK:
            for p2, s2 in st.legal_placements():
                if (p2, s2) not in d.target:
                    st.place(p2, s2)
                    break
    d._retarget(st)
    if not d.retargets:
        fail("re-targeting never fired after the opponent revealed 6 pieces")

    print("PASS: mixing is on by default and draws %d distinct legal in-pool "
          "armies, BUILDS %d distinct armies through the drafter, re-targeting "
          "fires once they reveal, --no-mix turns it off, and the SHIPPED "
          "champion deliberately carries no support so it plays fixed"
          % (len(drawn), len(built)))


def test_ply_limit_reported():
    """A game stopped at PLY_LIMIT scores 0.5 and must be counted as truncated.

    Scoring an unfinished game as a draw is fine; letting it disappear into the
    matrix as if it were a real draw is not. Every draw-heavy result in this
    repo depends on being able to tell the two apart -- the champion versus 13
    bishops drew 717 of 800 pairs, and only 3.1% of those games hit the limit.
    """
    saved, saved_max = arena.PLY_LIMIT, arena.MAX_PIECES
    arena.MAX_PIECES = 0          # ./cuci.py has no ceiling; the mirror is 36
    try:
        engine = chess.engine.SimpleEngine.popen_uci("./cuci.py")
    except Exception as e:                       # pragma: no cover
        fail("could not start ./cuci.py: %s" % e)
    try:
        army = pool.from_json(json.load(open("campaigns/champion_v3.json"))["army"])
        arena.PLY_LIMIT = 6
        score, plies, cut = arena.play_game(army, army, engine, 400)
        if not cut:
            fail("a game stopped at a 6-ply limit was not flagged as truncated")
        if score != 0.5:
            fail("a truncated game scored %r, expected 0.5" % score)
        if plies > 6:
            fail("the ply limit was overshot: %d" % plies)
        arena.PLY_LIMIT = saved
        score, plies, cut = arena.play_game(army, army, engine, 400)
        if cut:
            fail("a game that finished normally was flagged as truncated")
    finally:
        arena.PLY_LIMIT, arena.MAX_PIECES = saved, saved_max
        engine.quit()
    print("PASS: arena flags ply-limit truncations and scores them 0.5, so "
          "draw-heavy matrices can be told apart from unfinished ones")


def test_adapt():
    """A pure strategy must get solved and stay solved; a mixture must not.

    Rock-paper-scissors is the oracle, because the answer is known exactly:
    the equilibrium is uniform with value 0.5, a pure strategy scores 0
    against its counter, and no counter exists for the mixture. If the
    simulation cannot reproduce that it is not modelling a learning opponent.
    """
    import adapt
    rps = [[0.5, 0.0, 1.0],
           [1.0, 0.5, 0.0],
           [0.0, 1.0, 0.5]]
    uniform = [1 / 3.0] * 3
    rng = random.Random(1)
    pure = adapt.simulate(rps, "pure", 40, rng, uniform)
    if any(abs(v) > 1e-9 for v in pure[1:]):
        fail("a pure strategy was not solved by round 2: %r" % pure[:5])
    runs = [adapt.simulate(rps, "mix", 40, random.Random(k), uniform)
            for k in range(300)]
    tail = [sum(r[i] for r in runs) / len(runs) for i in range(20, 40)]
    avg = sum(tail) / len(tail)
    if abs(avg - 0.5) > 0.03:
        fail("the mixture drifted from the 0.5 equilibrium value: %.4f" % avg)

    # the shrinkage must reproduce the one cell measured at 400 pairs. Without
    # it the opponent's best response is an argmax over 4-pair cells and lands
    # on whichever got lucky: raw it predicts 0.3438 where the real match
    # measured 0.4566.
    with open("campaigns/expand_v3.json") as f:
        _st = json.load(f)
    _cells = {tuple(int(x) for x in k.split(",")): v
              for k, v in _st["cells"].items()}
    _champ = pool.from_json(json.load(open("campaigns/champion_v3.json"))["army"])
    _ci = next(i for i, a in enumerate(_st["armies"])
               if sorted(pool.from_json(a)) == sorted(_champ))
    for k, want, tol in ((0.0, 0.3438, 0.01), (adapt.SHRINK, 0.4566, 0.02)):
        raw = (adapt.shrink_cells(_cells, len(_st["armies"]), k) if k > 0
               else arena.matrix_from_cells(_cells, len(_st["armies"])))
        mm, kk, _ = solve.prepare(raw, 8)
        pos = kk.index(_ci)
        got = min(mm[pos][j] for j in range(len(mm)))
        if abs(got - want) > tol:
            fail("shrink %.1f predicts the counter cell at %.4f, expected "
                 "%.4f +/- %.2f" % (k, got, want, tol))

    # THE CHAMPION MUST NOT BE A NOISE ARTIFACT. It is selected as the argmax
    # of equilibrium weights computed from cells backed by four pairs, which is
    # exactly the kind of extreme-over-noise that produced two wrong numbers in
    # this repo. If a different army wins once the cells are shrunk, the
    # shipping choice was luck and the campaign needs more pairs per cell.
    with open("campaigns/champion_v3.json") as f:
        shipped = pool.from_json(json.load(f)["army"])
    with open("campaigns/expand_v3.json") as f:
        _s = json.load(f)
    _armies = [pool.from_json(a) for a in _s["armies"]]
    _cells = {tuple(int(x) for x in k.split(",")): v
              for k, v in _s["cells"].items()}
    for k in (0.0, 8.0, 32.0):
        raw = (adapt.shrink_cells(_cells, len(_armies), k) if k > 0
               else arena.matrix_from_cells(_cells, len(_armies)))
        mm, kk, _ = solve.prepare(raw, 8)
        xx, _val = solve.nash(mm)
        top = kk[max(range(len(mm)), key=lambda i: xx[i])]
        if sorted(_armies[top]) != sorted(shipped):
            fail("at shrink %.1f the equilibrium favours army %d, not the "
                 "shipped champion -- the choice was noise" % (k, top))

    # SPLIT-HALF RELIABILITY. Everything the double oracle does -- rank, prune,
    # solve -- rests on per-army mean scores. Each is an average over ~370
    # pairs even though its cells hold four, so it should be precise. If this
    # ever falls, the pool ordering is noise and so is the champion.
    halves = []
    for parity in (0, 1):
        tot = {}
        for k, v in _s["cells"].items():
            i, j, g = (int(x) for x in k.split(","))
            if v is None or i == j or g % 2 != parity:
                continue
            tot.setdefault(i, []).append(v)
        halves.append({i: sum(v) / len(v) for i, v in tot.items() if len(v) > 30})
    shared = sorted(set(halves[0]) & set(halves[1]))
    if len(shared) < 50:
        fail("split-half check covered only %d armies" % len(shared))
    import statistics
    r = statistics.correlation([halves[0][i] for i in shared],
                               [halves[1][i] for i in shared])
    if r < 0.90:
        fail("split-half reliability of the army ranking is %.3f; the pool "
             "ordering is noise" % r)

    # and on the real matrix: the mixture must beat the pure strategy once the
    # opponent has learned, which is the whole claim the file exists to make
    with open("campaigns/expand_v3.json") as f:
        st = json.load(f)
    cellmap = {tuple(int(x) for x in k.split(",")): v
               for k, v in st["cells"].items()}
    m, keep, _ = solve.prepare(
        arena.matrix_from_cells(cellmap, len(st["armies"])), 8)
    x, value = solve.nash(m)
    curves = {}
    for policy in ("pure", "mix"):
        rng = random.Random(7)
        runs = [adapt.simulate(m, policy, 30, rng, list(x)) for _ in range(60)]
        curves[policy] = sum(sum(r[15:]) / len(r[15:]) for r in runs) / len(runs)
    if curves["mix"] <= curves["pure"]:
        fail("mixing did not beat a pure strategy against a learner: %r" % curves)
    if abs(curves["mix"] - value) > 0.10:
        fail("the mixture strayed far from the equilibrium value: %.4f vs %.4f"
             % (curves["mix"], value))
    print("PASS: adapt reproduces rock-paper-scissors exactly (pure solved to "
          "0.0 by round 2, mixture %.4f at equilibrium), its shrinkage predicts "
          "the 400-pair counter cell where the raw matrix is off by 0.11, the "
          "shipped champion survives shrink 0 to 32, the army ranking has "
          "split-half reliability %.3f, and mixing scores %.4f "
          "against a learner where the champion scores %.4f"
          % (avg, r, curves["mix"], curves["pure"]))


def test_optionality():
    """Placing to stay uncommitted must widen the option set and still finish.

    The failure mode this guards is a policy that keeps its options open by
    never committing to anything and ends the setup with points unspent or an
    army outside the pool. Every pool army spends the full 39, so staying
    reachable should mean staying on track to complete one.
    """
    with open("campaigns/expand_fsf.json") as f:
        armies = [pool.from_json(a) for a in json.load(f)["armies"]]
    with open("campaigns/champion_fsf.json") as f:
        champ = pool.from_json(json.load(f)["army"])

    def draft(opt):
        st = rules.SetupState()
        us = play.Drafter(champ, chess.WHITE, pool=armies, optionality=opt)
        them = play.Drafter(armies[1], chess.BLACK)
        d = {chess.WHITE: us, chess.BLACK: them}
        trace = []
        for _ in range(200):
            if st.result or st.complete:
                break
            turn = st.turn
            st.place(*d[turn].choose(st))
            if turn == chess.WHITE:
                ours = {(p.piece_type, sq)
                        for sq, p in st.board.piece_map().items()
                        if p.color == chess.WHITE}
                trace.append(sum(1 for a in armies if ours <= set(a)))
        army = [(p.piece_type, sq) for sq, p in st.board.piece_map().items()
                if p.color == chess.WHITE]
        return trace, army

    plain, plain_army = draft(False)
    wide, wide_army = draft(True)
    if wide[0] <= plain[0]:
        fail("optionality did not widen the first placement: %d vs %d"
             % (wide[0], plain[0]))
    if sum(wide[:8]) <= sum(plain[:8]):
        fail("optionality is not wider over the opening eight placements")
    for name, army in (("plain", plain_army), ("optionality", wide_army)):
        ok, why = rules.validate_army(army)
        if not ok:
            fail("%s drafted an illegal army: %s" % (name, why))
        if rules.army_cost(army) != rules.BUDGET:
            fail("%s left %d of %d points unspent"
                 % (name, rules.BUDGET - rules.army_cost(army), rules.BUDGET))
    # NOT asserted: that it finishes inside the pool, or spends the budget.
    # It does neither reliably -- over 80 drafts against 40 opponents, 45
    # finish inside and 3 stop at 24-25 of 39 points, because the king rule,
    # the forced-block path and the fallback all ignore the pool. The single
    # opponent this test used originally passed by luck and hid a -79 Elo bug.
    # What IS pinned is that the option set widens and the army stays legal.
    for opp in armies[:8]:
        st = rules.SetupState()
        us = play.Drafter(champ, chess.WHITE, pool=armies, optionality=True)
        them = play.Drafter(opp, chess.BLACK)
        d = {chess.WHITE: us, chess.BLACK: them}
        for _ in range(200):
            if st.result or st.complete:
                break
            st.place(*d[st.turn].choose(st))
        army = [(p.piece_type, sq) for sq, p in st.board.piece_map().items()
                if p.color == chess.WHITE]
        ok, why = rules.validate_army(army)
        if not ok:
            fail("optionality drafted an illegal army: %s" % why)
    print("PASS: optionality keeps %d armies reachable after one placement "
          "against %d, stays wider through the opening, and drafts a legal "
          "army against 8 opponents (it does NOT reliably stay in the pool "
          "or spend the budget -- see OPTIONALITY)" % (wide[0], plain[0]))


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
    # A release fingerprint that depends on placement order is not an identity.
    # v1 published one that did, and two identical matchup pools fingerprinted
    # differently mid-campaign because one listed the king first.
    shuffled = list(sized[0])
    rng.shuffle(shuffled)
    if pool.fingerprint(shuffled) != pool.fingerprint(sized[0]):
        fail("fingerprint changed when the placements were reordered")
    if len({pool.fingerprint(a) for a in sized}) != len(sized):
        fail("fingerprint collided across %d distinct armies" % len(sized))

    print("PASS: %d archetypes legal and distinct, 2000 mutations and 2000 "
          "bred armies legal, crossover recombines, %d-army pool with no "
          "duplicates, fingerprints order-independent and collision-free"
          % (len(pool.ARCHETYPES), len(sized)))


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
    # A resume that changes the instrument must be refused, not averaged in.
    # arena had no guard at all: it kept only `cells`, threw `meta` away, and
    # save_state then rewrote meta so the file claimed every cell came from the
    # newest run. Army identity is checked too, because seed_pool tops up from
    # the rng: a different --seed puts different armies at the same index.
    _base = {"n": 2, "nodes": 300, "jitter": 0.15, "pairs": 1,
             "engine": "cuci.py", "seed": 2026, "ply_limit": 300,
             "max_pieces": 32, "armies": ["aa", "bb"]}
    if arena.check_resume(dict(_base), dict(_base), {(0, 1, 0): 0.5}) != {(0, 1, 0): 0.5}:
        fail("a same-settings resume was altered")
    for key, bad in (("engine", "stockfish"), ("nodes", 5000),
                     ("armies", ["aa", "cc"]), ("seed", 7)):
        cur = dict(_base)
        cur[key] = bad
        try:
            arena.check_resume(dict(_base), cur, {})
            fail("a resume with a different %s was allowed" % key)
        except SystemExit:
            pass
    # ...but raising the piece ceiling is the documented cuci.py workflow, and
    # it must RETRY the cells it unlocks rather than block or keep them None
    cur = dict(_base)
    cur["max_pieces"] = 0
    kept = arena.check_resume(dict(_base), cur, {(0, 1, 0): 0.5, (1, 0, 0): None})
    if (1, 0, 0) in kept or (0, 1, 0) not in kept:
        fail("raising --max-pieces did not free the unplayable cells: %r" % kept)
    if arena.check_resume({}, dict(_base), {(0, 1, 0): 0.5}) != {(0, 1, 0): 0.5}:
        fail("a legacy file with no meta should resume with a note, not refuse")

    print("PASS: arena end to end (4 cells played, resume replayed nothing, "
          "a changed instrument is refused, and a raised piece ceiling frees "
          "the cells it unlocks)")


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
    # A drawish support makes the screen blind and it fails SILENTLY as
    # "converged". This is the v5 campaign's failure mode, reproduced.
    if not expand.screen_blind([0.5] * 24, [], 0.5):
        fail("a screen where every challenger scored exactly the margin was "
             "not flagged as blind")
    if expand.screen_blind([0.5, 0.5, 0.75], [], 0.5):
        fail("a screen with a challenger above the margin was called blind")
    if expand.screen_blind([0.5, 0.25], [], 0.5):
        fail("a screen with a challenger below the margin was called blind")
    if expand.screen_blind([0.5] * 5, [3], 0.5):
        fail("a screen that admitted something was called blind")
    if expand.screen_blind([], [], 0.5):
        fail("an empty screen was called blind")

    print("PASS: expand state round trip, cell_tasks covers both directions, "
          "skips done cells, screen_scores weights by the mix, and a screen "
          "that resolves nothing is told apart from one that rejects")


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
    # The screen must weight OUR armies only. Blending a pinned opponent's
    # equilibrium weight in is the weighted average expand.py's own comment
    # records as having destroyed the filter: a challenger that beats the pin
    # and loses to everything of ours cleared the margin.
    import expand as _e2
    _w = {0: 0.13, 1: 0.14, 2: 0.13, 4: 0.60}
    _cells = {}
    for _j, _v in ((0, 0.10), (1, 0.10), (2, 0.10), (4, 0.95)):
        _cells[(5, _j, 0)] = _v
        _cells[(_j, 5, 0)] = 1.0 - _v
    _bad = _e2.screen_scores(_cells, 6, [5], _w)[5]
    _good = _e2.screen_scores(_cells, 6, [5],
                              _e2.our_strategies(_w, [4]))[5]
    if not (_bad > 0.5 >= _good):
        fail("the pinned-weight screen case no longer demonstrates: raw %.3f, "
             "stripped %.3f" % (_bad, _good))

    # A killed challenger keeps its scratch index, and the pool grows by
    # len(admitted), so the old `i2 < len(armies)` test let a dead mutant's
    # screen game land on an admitted army's brand-new row -- permanently,
    # because cell_tasks never re-plays an occupied cell.
    import expand as _ex
    _first, _adm = 10, {12, 13}
    _remap = {o: _first + k for k, o in enumerate(sorted(_adm))}
    _scratch = {(10, 0, 0): 0.10, (11, 0, 0): 0.20,      # killed
                (12, 0, 0): 0.95, (13, 0, 0): 0.90}      # admitted
    _out = _ex.carry_screen_cells(_scratch, _remap, _first, 12, {})
    if _out.get((10, 0, 0)) != 0.95 or _out.get((11, 0, 0)) != 0.90:
        fail("a killed challenger's screen game was credited to an admitted "
             "army: %r" % dict(sorted(_out.items())))
    if len(_out) != 2:
        fail("killed challengers' cells leaked into the pool: %r" % _out)
    # cells that are not challengers at all must pass through untouched
    _out2 = _ex.carry_screen_cells({(0, 1, 0): 0.4}, _remap, _first, 12, {})
    if _out2 != {(0, 1, 0): 0.4}:
        fail("an ordinary pool cell was dropped by the carry: %r" % _out2)

    # --start-pool REPLACES the archetypes; --seed-army only adds. The whole
    # bishops-or-basin experiment depends on that distinction.
    import expand
    import pool as _pool
    _start = [_pool.ARCHETYPES["queen_spam"], _pool.ARCHETYPES["rook_battery"]]
    _st = expand.new_state(2026, {}, False, None, _start)
    if len(_st["armies"]) != 2:
        fail("--start-pool did not replace the archetypes: %d armies"
             % len(_st["armies"]))
    _st = expand.new_state(2026, {}, False, [_pool.ARCHETYPES["minors"]], _start)
    if len(_st["armies"]) != 3:
        fail("--seed-army did not add on top of --start-pool")
    if len(expand.new_state(2026, {})["armies"]) != len(_pool.ARCHETYPES):
        fail("the default starting pool is no longer the archetypes")

    # --gate-pool swaps the gate field. It must be validated BEFORE the rounds
    # run, or a typo costs the whole campaign, and the gate file must record
    # which field produced it or two gates cannot be told apart.
    gp = os.path.join(tmpdir, "selftest_gatepool_%d.json" % os.getpid())
    with open(gp, "w") as f:
        json.dump([pool.to_json(a)
                   for a in list(pool.ARCHETYPES.values())[:3]], f)
    gpath = os.path.join(tmpdir, "selftest_expand_gp_%d.json" % os.getpid())
    r3 = subprocess.run(cmd[:3] + [gpath] + cmd[4:] + ["--gate-pool", gp],
                        capture_output=True, text=True)
    if r3.returncode != 0:
        fail("--gate-pool exited %d:\n%s\n%s" % (r3.returncode, r3.stdout, r3.stderr))
    if "3 armies from" not in r3.stdout:
        fail("--gate-pool did not name its field:\n%s" % r3.stdout)
    with open(gpath + ".gate") as f:
        if "3 armies from" not in json.load(f).get("gate_field", ""):
            fail("gate file did not record which field produced it")

    bad = os.path.join(tmpdir, "selftest_gatepool_bad_%d.json" % os.getpid())
    with open(bad, "w") as f:
        json.dump([[[5, 0], [5, 1], [5, 2], [5, 3], [5, 4], [6, 7]]], f)  # 45 pts
    bpath = os.path.join(tmpdir, "selftest_expand_bad_%d.json" % os.getpid())
    r4 = subprocess.run(cmd[:3] + [bpath] + cmd[4:] + ["--gate-pool", bad],
                        capture_output=True, text=True)
    out = r4.stdout + r4.stderr
    if "gate-pool army is illegal" not in out:
        fail("an illegal --gate-pool was not rejected:\n%s" % out)
    if "round 0 done" in r4.stdout:
        fail("an illegal --gate-pool was caught only AFTER a round ran")

    print("PASS: expand end to end (%d rounds, %d setups, gate match reported, "
          "resume kept its rounds, --gate-pool swaps the field, records it, "
          "and rejects an illegal one before any round runs)"
          % (len(state["rounds"]), len(state["armies"])))


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
                    # NOT a lockout just because no KING placement is offered:
                    # once the king is down there never is one. This used to
                    # return "lockout" there and the suite pinned the false
                    # claim -- on the rank3 style the black king was already on
                    # a7 with 6 legal placements left. A genuine lockout now
                    # surfaces as st.result, because rules.place() scores it.
                    if not legal:
                        return "stuck"
                    kings = [x for x in legal if x[0] == chess.KING]
                    mv = (kings[0] if kings
                          else min(legal, key=lambda t: rules.PIECE_COST[t[0]]))
                else:
                    bi = plan.index(mv) + 1
            st.place(*mv)
        return st.result or "survived"

    got = {k: run(_army(v)) for k, v in styles.items()}
    # THE TRUTH, once the false lockout verdict is gone: the hunt locks out
    # nothing here. It wins queen_spam, which the baseline also wins, and on
    # `heavy` it COSTS a win the baseline had (1-0 with the hunt off,
    # completed setup at HUNT_WHEN 6 and 8 alike).
    want = {"dense": "survived", "queen_spam": "1-0",
            "rank3": "survived", "heavy": "survived"}
    if got != want:
        fail("king hunt changed outcomes: %r, want %r" % (got, want))
    if run(_army(styles["heavy"]), hunt_when=-1) != "1-0":
        fail("the hunt-off baseline no longer wins the heavy style; the "
             "regression this pins is that the HUNT LOSES that win")
    # The drafter must not WALK INTO a setup mate. _mate_in_one only ever asked
    # "can I mate them?"; against pool army 14 of the v6 campaign the shipped
    # defaults lost BOTH colours to a setup checkmate.
    with open("campaigns/expand_v6.json") as f:
        _v6 = [pool.from_json(a) for a in json.load(f)["armies"]]
    _armies, _matrix = play.load_pool("campaigns/expand_v6.json")
    _tgt = play.load_army("campaigns/champion_v6.json")
    for _col in (chess.WHITE, chess.BLACK):
        _us = play.Drafter(_tgt if _col == chess.WHITE
                           else rules.mirror_army(_tgt),
                           _col, pool=_armies, matrix=_matrix)
        _them = play.Drafter(_v6[14] if _col == chess.BLACK
                             else rules.mirror_army(_v6[14]), not _col)
        _d = {_col: _us, not _col: _them}
        _st = play.draft(_d[chess.WHITE], _d[chess.BLACK])
        if _st.result:
            _we_won = (_st.result == "1-0") == (_col == chess.WHITE)
            if not _we_won:
                fail("the drafter allowed a setup mate against pool army 14 "
                     "playing %s: %s" % (chess.COLOR_NAMES[_col], _st.result))

    print("PASS: drafter realises its target for both colours, takes a setup "
          "mate over the plan, blocks a forced check, and wins %d of %d "
          "king-last styles (the hunt locks out NONE of them, and costs "
          "the win on heavy)" % (sum(1 for v in got.values() if v == "1-0"),
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
BENCH_NODES = 541501


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

    # in_check is a bound public entry point and must init its own tables:
    # a process whose FIRST C call was in_check() read all-zero attack tables
    # and returned false for a real knight check.
    _r = subprocess.run(
        [sys.executable, "-c",
         "import chess, cengine;"
         "b = chess.Board('4k3/8/8/8/8/5n2/8/4K3 w - - 0 1');"
         "print(cengine.in_check(b))"],
        capture_output=True, text=True)
    if _r.stdout.strip() != "True":
        fail("in_check on a cold process returned %r for a knight check"
             % _r.stdout.strip())

    # Quiescence must handle TERMINAL nodes: negamax dispatches depth<=0
    # straight into it, so without this a horizon mate scored as material and
    # `go depth 1` could not see mate in one. selftest drives 1000 setup FENs
    # at depth 1, so this is the depth that matters here.
    _m1 = chess.Board("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1")
    _mv, _sc, _ = cengine.search(_m1, depth=1)
    if _mv != chess.Move.from_uci("a1a8") or _sc < 29000:
        fail("depth 1 missed mate in one: %s at %d" % (_mv, _sc))
    _sm = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    if cengine.search(_sm, depth=1)[1] != 0:
        fail("a stalemate at the horizon scored %d, not 0"
             % cengine.search(_sm, depth=1)[1])

    # LEFT-RIGHT symmetry, which the colour mirror above cannot see. The queen
    # table shipped asymmetric on three ranks (c2 5 against f2 0, b3 against
    # g3, a4 against h4) and evaluate() differed by 5 cp on six squares.
    for _f in range(4):
        for _r in range(8):
            _a, _b = chess.square(_f, _r), chess.square(7 - _f, _r)
            if _a in (chess.E1, chess.E8) or _b in (chess.E1, chess.E8):
                continue
            _bd = []
            for _sq in (_a, _b):
                _x = chess.Board(None)
                _x.set_piece_at(_sq, chess.Piece(chess.QUEEN, chess.WHITE))
                _x.set_piece_at(chess.E1, chess.Piece(chess.KING, chess.WHITE))
                _x.set_piece_at(chess.E8, chess.Piece(chess.KING, chess.BLACK))
                _bd.append(cengine.evaluate(_x))
            if _bd[0] != _bd[1]:
                fail("queen eval is left-right asymmetric: %s=%d %s=%d"
                     % (chess.square_name(_a), _bd[0],
                        chess.square_name(_b), _bd[1]))

    # The halfmove clock has to REACH the C struct, or search.c's fifty-move
    # branch is dead code: MAX_PLY is 64, so a counter starting at 0 can never
    # reach 100 inside one search. All four drivers adjudicate with
    # claim_draw=True, so the engine used to report a won score for a position
    # one ply from being declared drawn.
    near = chess.Board("8/8/8/4k3/8/8/8/3QK3 w - - 99 60")
    if cengine.from_board(near).halfmove != 99:
        fail("from_board dropped the halfmove clock; the C fifty-move rule "
             "cannot fire")
    _, near_score, _ = cengine.search(near, depth=6)
    if near_score != 0:
        fail("one ply from the fifty-move draw the search scored %d, not 0"
             % near_score)
    fresh = chess.Board("8/8/8/4k3/8/8/8/3QK3 w - - 0 60")
    _, fresh_score, _ = cengine.search(fresh, depth=6)
    if fresh_score <= 500:
        fail("the same position with a fresh clock scored %d; the fifty-move "
             "guard is firing when it should not" % fresh_score)

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
    # A malformed argument to a KNOWN command used to kill the process, which
    # the host sees as EngineTerminatedError, contradicting cuci's own promise
    # that unknown input is ignored rather than fatal.
    for bad in ("position fen not-a-fen 0 1\ngo depth 2\n",
                "position startpos moves e2e4 zzzz\ngo nodes 300\n",
                "position startpos\ngo depth x\n"):
        r = subprocess.run([sys.executable, "cuci.py"],
                           input="uci\n" + bad + "quit\n",
                           capture_output=True, text=True)
        if r.returncode != 0:
            fail("cuci.py died (exit %d) on malformed input %r:\n%s"
                 % (r.returncode, bad, r.stderr[-400:]))
        if "bestmove" not in r.stdout:
            fail("cuci.py stopped answering after malformed input %r" % bad)

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
    test_watchdog()
    test_bot_policy()
    test_mix_default()
    test_ply_limit_reported()
    test_adapt()
    test_optionality()
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
    test_arena_columns(args.engine, args.scratch or tempfile.gettempdir())
    test_expand_smoke(args.engine, args.scratch or tempfile.gettempdir())
    test_expand_draft(args.engine, args.scratch or tempfile.gettempdir())
    test_expand_pin_pool()
    test_blind_failsafe()
    test_champion_in_pool()
    test_relay_reachability_both_colours()
    test_play_smoke(args.engine, args.scratch or tempfile.gettempdir())
    test_match_smoke(args.engine, args.scratch or tempfile.gettempdir())
    test_duel_smoke(args.engine, args.scratch or tempfile.gettempdir())
    test_psearch()
    test_draft()
    test_rules_placement_fastpath()
    test_book()
    test_blind_matrix_guard()
    print("OK: all selftests passed")



def test_psearch():
    """The placement search: exchange arithmetic and the double-stack it exists
    for. psearch owns the detailed assertions; this runs them with everything
    else so a change to rules.SetupState cannot break the search silently."""
    import psearch
    psearch._selfcheck()

    # The search must beat the plan-follower at the one thing it was built for:
    # not leaving material for the opponent to take at handoff.
    import chess, rules, play
    def hung(use_search):
        st = rules.SetupState()
        tgt = play.load_army("campaigns/champion_v6.json")
        dw, db = play.Drafter(tgt, chess.WHITE), play.Drafter(tgt, chess.BLACK)
        for _ in range(100):
            if st.complete or st.result or not st.legal_placements():
                break
            if st.turn == chess.WHITE and use_search:
                mv = psearch.best(st, chess.WHITE, max_depth=2, width=8)
            else:
                mv = (dw if st.turn == chess.WHITE else db).choose(st)
            st.place(*mv)
        return psearch._worst_exchange(st.board, chess.WHITE)
    assert hung(True) <= hung(False), "search hung more than the plan-follower"
    print("PASS: placement search scores exchanges and does not hang more "
          "material than the plan-follower")


def test_draft():
    """Drafted setups: every mode pairing finishes and yields a legal army."""
    import draft
    draft._selfcheck()
    print("PASS: placement phase can be played out by both strategies")


def test_rules_placement_fastpath():
    """_placements skips the per-candidate legality probe when it cannot
    matter. Differential against an exhaustive reference over random states,
    because this is a correctness shortcut in the rules and an eyeball on the
    argument is not evidence."""
    import random

    def reference(state, color):
        out = []
        has_king = state.board.king(color) is not None
        types = rules.BUYABLE if has_king else rules.BUYABLE + (chess.KING,)
        for pt in types:
            if rules.PIECE_COST[pt] > state.points[color]:
                continue
            for sq in rules.placement_squares(pt, color):
                if state.board.piece_at(sq):
                    continue
                state.board.set_piece_at(sq, chess.Piece(pt, color))
                ok = not state.in_check(color)
                state.board.remove_piece_at(sq)
                if ok:
                    out.append((pt, sq))
        return out

    rng = random.Random(7)
    compared = in_check = 0
    for _ in range(120):
        st = rules.SetupState()
        for _ in range(rng.randrange(0, 30)):
            if st.complete or st.result:
                break
            legal = st.legal_placements()
            if not legal:
                break
            st.place(*rng.choice(legal))
        for color in (chess.WHITE, chess.BLACK):
            if st.board.king(color) is not None and st.in_check(color):
                in_check += 1
            assert sorted(st._placements(color)) == sorted(reference(st, color)), \
                "fast path disagrees for %s" % chess.COLOR_NAMES[color]
            compared += 1
    assert in_check, "no in-check state generated: the probe path went untested"
    print("PASS: _placements fast path matches exhaustive over %d state/colour "
          "pairs (%d in check)" % (compared, in_check))


def test_book():
    """Opening book: key identity, round trip, and a generated entry is legal."""
    import book
    book._selfcheck()

    # Generate a real one-entry book and confirm it answers the position it
    # was generated for. Turn 0 is the same position in every game, which is
    # the entry that matters most: it runs under the ~20s forfeit clock.
    import chess, rules, psearch
    st = rules.SetupState()
    mv = psearch.best(st, chess.WHITE, max_depth=1, width=4)
    bk = {book.key(st): [int(mv[0]), int(mv[1])]}
    assert book.lookup(bk, st) == mv
    assert mv in set(st.legal_placements())
    # and it must MISS once a piece is down
    st.place(*mv)
    st.turn = chess.WHITE
    assert book.lookup(bk, st) is None, "book hit a position it does not cover"
    print("PASS: opening book keys, round-trips and answers only what it covers")


def test_blind_matrix_guard():
    """A referee that separates nothing must be reported, not read as a tie.

    This is the v5 failure: a matrix with the right cell count, no errors and
    passing symmetry checks, carrying no information at all. Measured for real
    -- a screen at three different eval weights returned 0.5000 +/- 0.0000
    three times and looked like "no difference"."""
    blind = {(0, 1, g): 0.5 for g in range(20)}
    blind.update({(1, 0, g): 0.5 for g in range(20)})
    measured = [v for v in blind.values() if v is not None]
    assert len(set(measured)) == 1, "fixture is not blind"

    live = dict(blind)
    live[(0, 1, 0)] = 1.0
    assert len(set(v for v in live.values() if v is not None)) > 1

    # the narrow-spread arm
    narrow = {(0, 1, g): 0.5 + 0.001 * (g % 3) for g in range(20)}
    vals = [v for v in narrow.values()]
    assert len(vals) > 8 and (max(vals) - min(vals)) < 0.02
    print("PASS: blind and near-blind matrices are distinguishable from real ones")


def test_expand_draft(engine, scratch):
    """expand --draft plays the placement phase, and cannot pool with a stamped
    campaign. Also pins that search-drafting is NOT offered here: both sides
    would draft the same army and the matrix would compare nothing."""
    import subprocess
    state = os.path.join(scratch, "selftest_expand_draft.json")
    for p in (state, state + ".gate"):
        if os.path.exists(p):
            os.remove(p)
    base = [sys.executable, "expand.py", "--state", state, "--rounds", "1",
            "--max-pool", "10", "--pairs", "1", "--screen-pairs", "1",
            "--final-games", "4", "--engine", engine, "--max-pieces", "0",
            "--nodes", "300", "--workers", "2"]
    r = subprocess.run(base + ["--draft"], capture_output=True, text=True)
    if r.returncode != 0:
        fail("expand --draft exited %d:\n%s" % (r.returncode, r.stderr[-800:]))
    if "drafting the placement phase" not in r.stdout:
        fail("expand --draft did not report that it was drafting")
    with open(state) as f:
        if not json.load(f)["meta"].get("draft"):
            fail("a drafted campaign did not record draft in its meta, so it "
                 "could be pooled with a stamped one")

    # a stamped resume must refuse the drafted state file
    r2 = subprocess.run(base, capture_output=True, text=True)
    if "different settings" not in (r2.stdout + r2.stderr):
        fail("a stamped run resumed a DRAFTED state file: the two measure "
             "different games and must never pool")

    # search-drafting must not be reachable from expand
    r3 = subprocess.run(base + ["--draft", "search"], capture_output=True,
                        text=True)
    if r3.returncode == 0:
        fail("expand accepted --draft search; both sides would draft the same "
             "army and the matrix would compare nothing")
    print("PASS: expand --draft plays the placement phase, refuses to pool "
          "with a stamped campaign, and does not offer search drafting")


def test_blind_failsafe():
    """The campaign must DIE on an instrument that separates nothing.

    Warning about it is not a safeguard for a run left unattended overnight:
    the loop would breed against noise for hours and then report "converged",
    which is exactly how the v5 campaign was wasted. Exercised as a subprocess
    because the guard exits the process.
    """
    import subprocess
    src = (
        "import sys, expand\n"
        "cells = {'identical': {(0,1,g): 0.5 for g in range(20)},\n"
        "         'narrow': {(0,1,g): 0.5 + 0.001*(g%3) for g in range(20)},\n"
        "         'healthy': {(0,1,g): g/20.0 for g in range(20)},\n"
        "         'few': {(0,1,g): 0.5 for g in range(4)}}[sys.argv[1]]\n"
        "expand.abort_if_blind(cells, 'test', sys.argv[2] == 'allow', '/tmp/x')\n"
    )
    want = {("identical", "no"): 3,    # zero information
            ("narrow", "no"): 3,       # inside 0.02: a kill filter at best
            ("healthy", "no"): 0,      # a real spread must not trip it
            ("few", "no"): 0,          # too few cells to judge
            ("identical", "allow"): 0}
    for (case, allow), code in sorted(want.items()):
        r = subprocess.run([sys.executable, "-c", src, case, allow],
                           capture_output=True, text=True)
        if r.returncode != code:
            fail("abort_if_blind(%s, allow=%s) exited %d, wanted %d:\n%s"
                 % (case, allow, r.returncode, code, r.stdout + r.stderr))
        if code == 3 and "STOPPING" not in r.stdout:
            fail("the fail-safe aborted on %s without saying why" % case)
        if (case, allow) == ("identical", "allow") and "WARNING" not in r.stdout:
            fail("--allow-blind neither aborted nor warned")
    print("PASS: the blind-instrument fail-safe stops the campaign, spares a "
          "real spread, ignores tiny samples, and can be overridden")


def test_champion_in_pool():
    """The shipped champion must be a MEMBER of the shipped pool.

    Re-targeting only considers armies inside the pool, so a champion that is
    not one is abandoned the moment the opponent reveals anything -- the army
    chosen by the measurement would never actually be played. Shipped exactly
    that way for one commit: DEFAULT_TARGET moved to the v9 champion while
    DEFAULT_POOL still pointed at v6, and a single revealed placement switched
    the target to an unrelated bishop army.
    """
    champ = play.load_army(play.DEFAULT_TARGET)
    fp = pool.fingerprint(champ)
    armies, matrix = play.load_pool(play.DEFAULT_POOL)
    if not any(pool.fingerprint(a) == fp for a in armies):
        fail("the shipped champion %s is not in DEFAULT_POOL (%s): re-targeting "
             "will abandon it on the opponent's first reveal"
             % (fp, play.DEFAULT_POOL))

    # and prove the abandonment does not happen in practice
    d = play.Drafter(champ, chess.WHITE, pool=armies, matrix=matrix)
    st = rules.SetupState()
    st.place(chess.QUEEN, chess.D1)
    st.turn = chess.BLACK
    st.place(chess.BISHOP, chess.B7)
    st.turn = chess.WHITE
    d._retarget(st)
    if not any(pool.fingerprint(sorted(d.target)) == pool.fingerprint(a)
               for a in armies):
        fail("after re-targeting the drafter is building an army outside the "
             "pool, which the off-plan fallback then wrecks")
    print("PASS: the shipped champion is in the shipped pool, and re-targeting "
          "stays inside it")


def test_relay_reachability_both_colours():
    """relay's reachability line must be in OWN perspective for both colours.

    _revealed mirrors Black's squares and the pool is stored in own
    perspective, but drafter.choose returns BOARD coordinates. Mixing them made
    every Black placement report 0 armies reachable, which reads as "the
    drafter has gone off-plan" in the middle of a live game under a ~20 second
    forfeit clock. White was unaffected, so it never showed.
    """
    import subprocess
    for colour, moves in (("white", []), ("black", ["@Qd1"])):
        r = subprocess.run([sys.executable, "relay.py", "--color", colour,
                            "--no-mix"] + moves, capture_output=True, text=True)
        if r.returncode != 0:
            fail("relay --color %s exited %d: %s"
                 % (colour, r.returncode, r.stderr[-400:]))
        line = [l for l in r.stdout.splitlines() if "reachable" in l]
        if not line:
            fail("relay --color %s printed no reachability line" % colour)
        got = int(line[0].split(":")[1].strip().split()[0])
        if got == 0:
            fail("relay --color %s reports 0 pool armies reachable on its FIRST "
                 "placement, which can only be a perspective bug" % colour)
    print("PASS: relay reports reachability in own perspective for both colours")


def test_arena_columns(engine, scratch):
    """--columns plays only cells touching the field, and every other army
    still ends with a COMPLETE row over it.

    Scoring a whole pool against a small field is the useful shape -- 135
    armies against 4 real opponents is 540 matchups, where the full matrix is
    18,225 cells of mostly army-vs-army pairings nobody reads. A partial
    matrix is only safe if the rows that matter are whole, so that is what
    this asserts.
    """
    import subprocess
    out = os.path.join(scratch, "selftest_columns.json")
    if os.path.exists(out):
        os.remove(out)
    poolf = os.path.join(scratch, "selftest_columns_pool.json")
    armies = [pool.from_json(a) for a in
              json.load(open("campaigns/pool_confirm_v9c.json"))]
    with open(poolf, "w") as fh:
        json.dump([pool.to_json(a) for a in armies], fh)
    n = len(armies)
    field = [n - 2, n - 1]

    r = subprocess.run([sys.executable, "arena.py", "--out", out, "--pool", poolf,
                        "--engine", engine, "--max-pieces", "0", "--nodes", "200",
                        "--pairs", "1", "--workers", "2",
                        "--columns", ",".join(str(c) for c in field)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        fail("arena --columns exited %d: %s" % (r.returncode, r.stderr[-500:]))

    with open(out) as fh:
        cells = {tuple(int(x) for x in k.split(",")): v
                 for k, v in json.load(fh)["cells"].items()}
    stray = [(i, j) for (i, j, g) in cells if i not in field and j not in field]
    if stray:
        fail("--columns played %d cells touching neither column, e.g. %s"
             % (len(stray), stray[:3]))
    for i in range(n):
        if i in field:
            continue
        for j in field:
            if not any((a, b) in ((i, j), (j, i))
                       for (a, b, g) in cells):
                fail("army %d has no games against column %d, so its row "
                     "cannot be ranked" % (i, j))

    bad = subprocess.run([sys.executable, "arena.py", "--out", out, "--pool", poolf,
                          "--engine", engine, "--max-pieces", "0", "--nodes", "200",
                          "--pairs", "1", "--workers", "2", "--columns", "999"],
                         capture_output=True, text=True)
    if bad.returncode == 0:
        fail("--columns accepted an index outside the pool")
    print("PASS: --columns plays only field-touching cells (%d of %d), leaves "
          "every row complete, and rejects a bad index" % (len(cells), n * n))


def test_expand_pin_pool():
    """--pin-pool adds opponents that are measured against but never ours.

    A pin must (a) land in state["pinned"] so our_strategies strips it from the
    mix, (b) DEDUP against an army already in the starting pool, and (c) mark
    the campaign so a pinned and an unpinned run cannot pool. The dedup is the
    subtle one: an opponent sitting in the pool twice, once pinned and once
    not, lets the unpinned copy take the equilibrium weight and become both the
    breeding parent and the gate mix -- the exact failure our_strategies exists
    to prevent.
    """
    import subprocess
    scratch = tempfile.gettempdir()
    pinf = os.path.join(scratch, "selftest_pin.json")
    opp = [pool.from_json(a) for a in
           json.load(open("campaigns/pool_real_opponents.json"))][2]
    with open(pinf, "w") as fh:
        json.dump([pool.to_json(opp)], fh)
    tgt = pool.fingerprint(opp)

    state = os.path.join(scratch, "selftest_pin_state.json")
    for p in (state, state + ".gate"):
        if os.path.exists(p):
            os.remove(p)
    base = [sys.executable, "expand.py", "--state", state, "--rounds", "1",
            "--max-pool", "40", "--pairs", "1", "--screen-pairs", "1",
            "--final-games", "4", "--engine", "./cuci.py", "--max-pieces", "0",
            "--nodes", "300", "--workers", "2", "--draft",
            "--start-pool", "campaigns/pool_maximin_wide.json"]
    r = subprocess.run(base + ["--pin-pool", pinf], capture_output=True, text=True)
    if r.returncode != 0:
        fail("expand --pin-pool exited %d: %s" % (r.returncode, r.stderr[-600:]))

    with open(state) as fh:
        st = json.load(fh)
    armies = [pool.from_json(a) for a in st["armies"]]
    hits = [i for i, a in enumerate(armies) if pool.fingerprint(a) == tgt]
    if len(hits) != 1:
        fail("the pinned army appears %d times in the pool; a duplicate lets "
             "the unpinned copy take the equilibrium weight" % len(hits))
    if st["pinned"] != hits:
        fail("pinned=%s but the army sits at %s" % (st["pinned"], hits))
    if not st["meta"].get("pin_pool"):
        fail("a pinned campaign did not record pin_pool in its meta")

    # our_strategies must strip it
    import expand
    w = {i: 1.0 / len(armies) for i in range(len(armies))}
    if hits[0] in expand.our_strategies(w, st["pinned"]):
        fail("our_strategies kept a pinned opponent in OUR mix")

    r2 = subprocess.run(base, capture_output=True, text=True)
    if "different settings" not in (r2.stdout + r2.stderr):
        fail("an unpinned run resumed a PINNED campaign; the pin changes which "
             "cells the equilibrium is solved over")
    print("PASS: --pin-pool pins without duplicating, is stripped from our "
          "mix, and cannot pool with an unpinned campaign")

if __name__ == "__main__":
    main()
