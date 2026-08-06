# Setup Chess

An engine for the chess.com variant [Setup Chess](https://www.chess.com/variants/setup-chess):
before play, each side spends **39 points** placing an army on its own first
three ranks (P=1, N=3, B=3, R=5, Q=9, king free and mandatory, duplicates
unlimited). Once both armies are down, it is ordinary chess.

The interesting half is the drafting. This repo builds a candidate pool of
armies, measures them against each other by playing games, solves for the
best mix, and plays the placement phase with the tactics that phase actually
has -- including two ways to win before move one.

Python is the harness; the move generator is C.

## What it found

**Bishops dominate.** The solved army is twelve bishops and three pawns, bred
by the expansion loop rather than hand-written. It beat the earlier
nine-bishop-plus-queen champion head to head by **+120.09 Elo
[+110.17, +130.20]** over 400 pairs:

```
  3 | . B B . . B P .
  2 | . B B P B P B .
  1 | B . B B K . B B
      a b c d e f g h
```

Against the twelve hand-written archetypes, sampled uniformly:

```
Games   | 1,820 of 2,000 (90 pairs unplayable)
Score   | 0.9346 +/- 0.0079
W/D/L   | 1,586 / 230 / 4
Elo     | +462.06 +/- 22.5   [+440.79, +485.88]
SPRT    | [0,4] LLR +153.155 -> ACCEPT H1
Pool    | 87 setups after 21 expansion rounds
TC      | 20,000 nodes fixed, 15% per-game jitter
Machine | Mac14,9 arm64, macOS, Stockfish 17
```

Four losses in 1,820 games. Read that as "much better than hand-written
guesses", not "strong": the archetype field includes deliberately bad armies
and one of them scores 0.016. See [Known limits](#known-limits).

**It holds at ten times the depth.** Re-gated at 200,000 nodes, the champion
army alone against the same archetype field:

```
Pairs   | 440 of 480 (archetype 3 unplayable, all 40 pairs)
Score   | 0.9324 +/- 0.0126
Pairs   | 341 swept / 79 at 0.75 / 20 drawn / 0 lost
Elo     | +455.82 +/- 35.0   [+423.88, +493.83]
SPRT    | [0,4] LLR +60.252 -> ACCEPT H1
TC      | 200,000 nodes fixed, 15% per-game jitter
Machine | Mac14,9 arm64, macOS, Stockfish 17, 10 workers
```

Not one pair lost in 880 games. Solving the 13-army matrix at this depth puts
**all the equilibrium weight on the champion**, exploitability 0.0000, so it is
a best response to the whole archetype field and not merely a good average.

Still not differenceable against the +462 above -- that gate sampled the
*solved mix*, this one plays the *champion army alone* -- so the same pool was
re-run at 20,000 nodes to isolate depth. **Depth changes nothing:**

```
20k    | 0.9301   +449.66 [+418.62, +486.36]
200k   | 0.9324   +455.82 [+423.88, +493.83]
Paired | +0.0023 +/- 0.0129 over 440 identically-indexed pairs
Games  | 122 of 440 pairs came out differently
```

Both node counts put the entire equilibrium on the champion, exploitability
0.0000, and both drop archetype 3 for the same piece-count reason. A tenth of
the search buys the same verdict, so the remaining gap between +449.66 and
+462.06 is the mix-versus-champion difference, not depth.

## Against a model of the real opponent

The archetypes are hand-written guesses. `play.py --opponent bot` is
chess.com's own setup policy rebuilt from its shipped client
(`docs/BOT_MODEL.md`): king to a corner on move one, then 16 pawns and 7
knights, because material is absent from its setup eval entirely. The champion
does not dominate it.

```
we are white | 0.6500 +/- 0.0318   +107.54 [+83.69, +132.41]   W/D/L  60/140/0
we are black | 0.8475 +/- 0.0320   +297.95 [+258.19, +345.27]  W/D/L 139/ 61/0
Games        | 200 per colour, 20,000 nodes, 15% jitter
Referee      | ./cuci.py -- 40 pieces is over Stockfish's ceiling
```

No losses in 400 games, but **201 of them drawn**: the pawn-and-knight wall is
genuinely hard to break, and at exactly 20,000 nodes with jitter off both
colours drew by threefold repetition. Compare 0.93 against the archetypes and
the gap is the point -- **the weak field, not the search depth, was what those
numbers were resting on.**

Three things about this measurement, because it is easy to over-read:

* **The error bars are conditional on ONE setup per colour.** Both drafters are
  deterministic, so the 200 games sample the chess phase and nothing else.
  Those intervals contain no drafting variance at all.
* **The referee is our own C core**, which measures -327 Elo weaker than
  Stockfish, because 16 of our pieces plus 24 of theirs is 40 on the board.
  Referee agreement is 0.888 Spearman over the archetypes, so it is usable, but
  this number is on a different instrument from the +455.82 gate and the two
  must not be pooled.
* **We move first in both colours** (checked: both handoff FENs give us the
  move), since the bot spends 24 placements to our 16 and so always places
  last. The colour asymmetry is therefore not a tempo effect.

`expand.py --seed-bot` breeds the pool against this army instead of only
against itself. Three parts, all load-bearing: the wall joins the starting
pool, it is **pinned** so the prune cannot drop it, and it takes half the
screen weight. Without the last one the whole thing is a no-op, because the
wall draws rather than wins, so the solver gives it no equilibrium weight and
the screen only ever plays challengers against the support.

Its 24 pieces put most matchups over Stockfish's ceiling, so this campaign has
to be refereed by our own core:

```bash
python3 expand.py --state campaigns/expand_bot.json --seed-bot \
  --engine ./cuci.py --max-pieces 0 --rounds 30 --workers 0
```

That is a different referee from every other campaign in `campaigns/`, so it is
a different instrument and a separate state file, and its results are not
comparable with the Stockfish-refereed numbers above.

That asymmetry is the interesting part. Re-targeting fired both times but
landed somewhere different: as White it came back to the pure champion, twelve
bishops and three pawns, and scored 0.65; as Black it swapped in a rook for two
bishops -- ten bishops, a rook, four pawns -- and scored 0.85. One setup per
colour is not enough to credit the rook for that, but it is the obvious thing
to test next.

## The setup phase has real tactics

Two ways the game ends before a single move is played, both verified against
the shipped chess.com client:

* **Checkmate during setup.** A placement can give check, and the checked
  side can only answer by placing a blocker inside its own three ranks. If
  nothing reaches, it is mate. Triggered live on chess.com's own analysis
  board with `@Qe1#` against a king on the third rank.
* **King lockout.** A player who ends setup without a king loses outright
  (the client says `"failed to set up his king"`). A king may not be placed
  on an attacked square, so covering every empty square in the opponent's
  zone wins without any checkmate at all.

The second one punishes the common habit of placing the king last. Measured
against an army covering 23 of the opponent's 24 zone squares:

| opponent style | outcome |
|---|---|
| dense, king last | survives -- its own 16 pieces block every ray |
| queen spam, king last | loses, setup checkmate |
| rank-3 rush, king last | loses, locked out |
| heavy, king last | loses, locked out |

Sparse armies get punished; dense ones shield themselves.

## Why a C core rather than Stockfish

Setup Chess positions are legal in the variant and illegal in *standard*
chess: sixteen pawns, nine bishops, up to 48 pieces on the board. python-chess
rejects them by ordinary-chess history rules, and Stockfish's data structures
assume 32 pieces -- the 42-piece pawn-wall mirror answers `depth 4` fine and
then **segfaults at 20,000 nodes** (measured threshold on this build: 36
pieces survive, 38 crash). That is why 9% of gate pairs are still unmeasured.

The C core is bitboard-based, so it has no piece-count ceiling:

```
Perft        | startpos(4) 197,281 exact; 8 setup positions to 42 pieces
Mismatches   | 0 against python-chess, node for node
Speed        | 41.5 Mnps vs python-chess's 1.66 (25x)
```

Published perft numbers assume castling, which this variant does not have,
so python-chess with the rights stripped is the reference.

## Layout

| file | what it is |
|---|---|
| `docs/RULES.md` | the rules, every line sourced, assumptions explicit |
| `docs/BOT_MODEL.md` | chess.com's own bot, decoded from its shipped client: king to a corner on move one, material ignored while drafting |
| `rules.py` | placement legality, points, FEN emission, validation |
| `pool.py` | archetype seeds, mutation and crossover operators |
| `arena.py` | fills the payoff matrix, engine vs engine, resumable |
| `solve.py` | equilibrium mix, best response, exploitability |
| `expand.py` | the double-oracle pool expansion loop; `--seed-bot` breeds against the modelled opponent |
| `stats.py` | Elo, confidence intervals, SPRT |
| `play.py` | drafts an army and plays the game out; `--opponent bot` is chess.com's own setup policy |
| `match.py` | paired full-game A/B for a drafting change |
| `duel.py` | engine versus engine over setup positions |
| `Constants.h`, `movegen.c`, `eval.c`, `search.c` | the C core |
| `cengine.py`, `cuci.py` | ctypes binding and the UCI front end |
| `selftest.py` | run before every commit |
| `campaigns/` | campaign state and gate results, in git on purpose |

## Getting started

```bash
./setup.sh
```

Installs dependencies, builds the C core and checks it with a perft oracle,
and verifies a UCI engine answers `uci`.

```bash
python3 selftest.py
```

```bash
python3 play.py --opponent classic
```

Plays one game each colour, setup through result. `--opponent stdin` reads
placements as `@Qd1` tokens for driving a game elsewhere.

Longer jobs, which take minutes to hours:

```bash
python3 arena.py --out ~/matrix.json --nodes 20000 --pairs 4 --workers 0
```

```bash
python3 expand.py --state ~/expand.json --rounds 30 --challengers 32 --pairs 4 --screen-pairs 2 --workers 0 --final-games 400
```

Both are resumable; Ctrl-C checkpoints and exits cleanly.

## Known limits

* **The baseline is weak.** +462 Elo is against hand-written archetypes, one
  of which scores 0.016 against the field. It is not a measurement against
  strong opposition.
* **9% of gate pairs are unmeasured**, and more games will not fix it. Those
  are the highest-piece-count matchups that Stockfish cannot survive. It is a
  coverage gap, not noise.
* ~~The champion gate is 20,000 nodes only~~ **now also measured at 200,000**:
  the champion scores 0.9324, +455.82 [+423.88, +493.83], and the 13-army
  equilibrium is pure on it. Twelve bishops are not a shallow-search artifact.
  The depth-only comparison is now done on a matched pool and comes out flat:
  +0.0023 +/- 0.0129 over 440 identically-indexed pairs. One caveat stands,
  and it is the important one: the field is still the same twelve hand-written
  archetypes, so a deeper search has only confirmed dominance over weak
  opposition. Depth was never the weak link in that claim -- the field is.
* **One whole archetype is missing from the depth gate.** Archetype 3 lost all
  40 of its pairs to Stockfish's piece ceiling, so 11 of 12 opponents are
  measured rather than a scattered 9%. Only one archetype offers real
  resistance at depth (0.6312); the rest sit above 0.87.
* **Best-response re-targeting still gives up a forced setup mate**, because
  the payoff matrix is measured by playing the *chess* phase from finished
  armies and cannot see setup tactics. It is on anyway, and CONFIRMED on the
  87-setup pool the defaults use: **+24.63 Elo [+18.06, +31.22]** over 1,187
  pairs, SPRT [0,4] LLR +8.101 -> ACCEPT H1 at full budget. `--no-pool`
  disables it. Teaching the matrix about the placement phase is the open work
  here, and would probably recover that forced mate on top.
* **That number is the re-run after the handoff turn-order fix**, and it was a
  real re-run: `match.py` plays from `handoff_fen()`, and the fix changed the
  outcome of **494 of the 1,187 pairs**. The pre-fix reading on the same pool
  was +29.63 [+23.46, +35.83]; the ranges overlap heavily, so the fix did not
  measurably change the effect, but the point estimate is about 5 Elo lower
  and only the post-fix one describes the shipping code. **The 19-setup pool's
  +17.13 [+12.36, +21.91] has NOT been re-run** and is still a pre-fix number.
  The champion gate never shared the problem: `arena.py` goes through
  `setup_fen()`, which did not change.
* **Re-targeting survives a 10x deeper search**, which is the only longer-TC
  result in the repo. At 200,000 nodes on the same 1,187 pairs it measures
  **+29.93 Elo [+23.66, +36.21]**, LLR +11.037 -> ACCEPT H1, against +24.63
  [+18.06, +31.22] at 20,000. The ranges overlap, so the honest reading is
  "no measured decay with depth", not "it gets better". 409 of the 1,187 pairs
  came out differently at the deeper search, so the two are genuinely separate
  instruments and are not pooled.
* ~~One rule is assumed~~ **Verified on the live board 2026-08-05**: a king
  may not be placed onto an attacked square, and non-king pieces may. The
  lockout tactic rests on real rules, not an assumption.
* **The chess phase does not always start with White**, which cost a live game
  before it was measured. A finished side *passes* rather than being skipped
  (the server writes `P` in the move list), so the turns keep alternating and
  whoever follows the final placement moves first. Verified over a full
  26-placement game against chess.com's own bot: White placed last, Black
  opened with `Qh3+`, and our FEN matched the server's byte for byte.
  `handoff_fen()` is the only correct source for this; `setup_fen()` gives
  White the move by convention because two finished armies carry no placement
  order.
* **The payoff matrix therefore always hands White the tempo**, while a real
  game hands it to whichever side the placement count lands on. Both colours
  are played in every pair so it cancels in aggregate, but the armies were
  never selected for the parity they will actually get. Unmeasured.
* **The pool is finite.** 87 armies after 21 expansion rounds, and it stopped
  because it filled rather than because it converged: at `--max-pool` every
  further round only prunes and re-admits at rising cost while exploitability
  has been pinned at 0 throughout. The equilibrium is now genuinely mixed over
  11 setups, which is a better sign than the old pure one, but the pool is
  still a sample of the space rather than a cover of it.
